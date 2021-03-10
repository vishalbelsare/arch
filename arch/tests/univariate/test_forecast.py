import datetime as dt
from itertools import product
import sys

import numpy as np
from numpy.random import RandomState
from numpy.testing import assert_allclose
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from arch.data import sp500
from arch.tests.univariate.test_variance_forecasting import preserved_state
from arch.univariate import (
    APARCH,
    ARX,
    EGARCH,
    FIGARCH,
    GARCH,
    HARCH,
    HARX,
    ConstantMean,
    ConstantVariance,
    EWMAVariance,
    MIDASHyperbolic,
    RiskMetrics2006,
    ZeroMean,
    arch_model,
)
from arch.univariate.mean import _ar_forecast, _ar_to_impulse

SP500 = 100 * sp500.load()["Adj Close"].pct_change().dropna()

MEAN_MODELS = [
    HARX(SP500, lags=[1, 5]),
    ARX(SP500, lags=2),
    ConstantMean(SP500),
    ZeroMean(SP500),
]

VOLATILITIES = [
    ConstantVariance(),
    GARCH(),
    FIGARCH(),
    EWMAVariance(lam=0.94),
    MIDASHyperbolic(),
    HARCH(lags=[1, 5, 22]),
    RiskMetrics2006(),
    APARCH(),
    EGARCH(),
]

MODEL_SPECS = list(product(MEAN_MODELS, VOLATILITIES))
IDS = [
    f"{str(mean).split('(')[0]}-{str(vol).split('(')[0]}" for mean, vol in MODEL_SPECS
]


@pytest.fixture(params=MODEL_SPECS, ids=IDS)
def model_spec(request):
    mean, vol = request.param
    mean.volatility = vol
    return mean


class TestForecasting(object):
    @classmethod
    def setup_class(cls):
        cls.rng = RandomState(12345)
        am = arch_model(None, mean="Constant", vol="Constant")
        data = am.simulate(np.array([0.0, 10.0]), 1000)
        data.index = pd.date_range("2000-01-01", periods=data.index.shape[0])
        cls.zero_mean = data.data

        am = arch_model(None, mean="AR", vol="Constant", lags=[1])
        data = am.simulate(np.array([1.0, 0.9, 2]), 1000)
        data.index = pd.date_range("2000-01-01", periods=data.index.shape[0])
        cls.ar1 = data.data

        am = arch_model(None, mean="AR", vol="Constant", lags=[1, 2])
        data = am.simulate(np.array([1.0, 1.9, -0.95, 2]), 1000)
        data.index = pd.date_range("2000-01-01", periods=data.index.shape[0])
        cls.ar2 = data.data

        am = arch_model(None, mean="HAR", vol="Constant", lags=[1, 5, 22])
        data = am.simulate(np.array([1.0, 0.4, 0.3, 0.2, 2]), 1000)
        data.index = pd.date_range("2000-01-01", periods=data.index.shape[0])
        cls.har3 = data.data

        am = arch_model(None, mean="AR", vol="GARCH", lags=[1, 2], p=1, q=1)
        data = am.simulate(np.array([1.0, 1.9, -0.95, 0.05, 0.1, 0.88]), 1000)
        data.index = pd.date_range("2000-01-01", periods=data.index.shape[0])
        cls.ar2_garch = data.data

    def test_ar_forecasting(self):
        params = np.array([0.9])
        forecasts = _ar_forecast(self.zero_mean, 5, 0, 0.0, params)
        expected = np.zeros((1000, 5))
        expected[:, 0] = 0.9 * self.zero_mean.values
        for i in range(1, 5):
            expected[:, i] = 0.9 * expected[:, i - 1]
        assert_allclose(forecasts, expected)

        params = np.array([0.5, -0.3, 0.2])
        forecasts = _ar_forecast(self.zero_mean, 5, 2, 0.0, params)
        expected = np.zeros((998, 8))
        expected[:, 0] = self.zero_mean.iloc[0:-2]
        expected[:, 1] = self.zero_mean.iloc[1:-1]
        expected[:, 2] = self.zero_mean.iloc[2:]
        for i in range(3, 8):
            expected[:, i] = (
                0.5 * expected[:, i - 1]
                - 0.3 * expected[:, i - 2]
                + 0.2 * expected[:, i - 3]
            )
        fill = np.empty((2, 5))
        fill.fill(np.nan)
        expected = np.concatenate((fill, expected[:, 3:]))
        assert_allclose(forecasts, expected[2:])

    def test_ar_to_impulse(self):
        arp = np.array([0.9])
        impulses = _ar_to_impulse(20, arp)
        expected = 0.9 ** np.arange(20)
        assert_allclose(impulses, expected)

        arp = np.array([0.5, 0.3])
        impulses = _ar_to_impulse(20, arp)
        comp = np.array([arp, [1, 0]])
        a = comp.copy()
        expected = np.ones(20)
        for i in range(1, 20):
            expected[i] = a[0, 0]
            a = a.dot(comp)
        assert_allclose(impulses, expected)

        arp = np.array([1.5, 0.0, -0.7])
        impulses = _ar_to_impulse(20, arp)
        comp = np.array([arp, [1, 0, 0], [0, 1, 0]])
        a = comp.copy()
        expected = np.ones(20)
        for i in range(1, 20):
            expected[i] = a[0, 0]
            a = a.dot(comp)
        assert_allclose(impulses, expected)

    def test_zero_mean_forecast(self):
        am = arch_model(self.zero_mean, mean="Zero", vol="Constant")
        res = am.fit()
        fcast = res.forecast(res.params, horizon=3, reindex=False)
        alt_fcast = res.forecast(horizon=3, reindex=False)
        assert_frame_equal(fcast.mean, alt_fcast.mean)
        assert_frame_equal(fcast.variance, alt_fcast.variance)
        assert_frame_equal(fcast.residual_variance, alt_fcast.residual_variance)

        fcast_reindex = res.forecast(res.params, horizon=3, reindex=True)
        assert_frame_equal(fcast.mean, fcast_reindex.mean.iloc[-1:])
        assert_frame_equal(fcast.variance, fcast_reindex.variance.iloc[-1:])
        assert_frame_equal(
            fcast.residual_variance, fcast_reindex.residual_variance.iloc[-1:]
        )
        assert fcast_reindex.mean.shape[0] == self.zero_mean.shape[0]

        assert np.all(np.asarray(np.isnan(fcast.mean[:-1])))
        assert np.all(np.asarray(np.isnan(fcast.variance[:-1])))
        assert np.all(np.asarray(np.isnan(fcast.residual_variance[:-1])))

        params = np.asarray(res.params)
        assert np.all(0.0 == fcast.mean.iloc[-1])
        assert_allclose(fcast.variance.iloc[-1], np.ones(3) * params[0])
        assert_allclose(fcast.residual_variance.iloc[-1], np.ones(3) * params[0])

        res = am.fit(last_obs=500)
        params = np.asarray(res.params)
        fcast = res.forecast(horizon=3, reindex=False)
        assert fcast.mean.shape == (501, 3)
        assert fcast.variance.shape == (501, 3)
        assert fcast.residual_variance.shape == (501, 3)
        assert np.all(np.asarray(np.isfinite(fcast.mean)))
        assert np.all(np.asarray(np.isfinite(fcast.variance)))
        assert np.all(np.asarray(np.isfinite(fcast.residual_variance)))

        assert np.all(np.asarray(0.0 == fcast.mean))
        assert_allclose(fcast.variance, np.ones((501, 3)) * params[0])
        assert_allclose(fcast.residual_variance, np.ones((501, 3)) * params[0])
        with pytest.raises(ValueError, match="horizon must be an integer >= 1"):
            res.forecast(horizon=0, reindex=False)

    def test_frame_labels(self):
        am = arch_model(self.zero_mean, mean="Zero", vol="Constant")
        res = am.fit()
        fcast = res.forecast(horizon=12, reindex=False)
        assert fcast.mean.shape[1] == 12
        assert fcast.variance.shape[1] == 12
        assert fcast.residual_variance.shape[1] == 12
        for i in range(1, 13):
            if i < 10:
                col = "h.0" + str(i)
            else:
                col = "h." + str(i)

            assert col in fcast.mean.columns
            assert col in fcast.variance.columns
            assert col in fcast.residual_variance.columns

    def test_ar1_forecast(self):
        am = arch_model(self.ar1, mean="AR", vol="Constant", lags=[1])
        res = am.fit()

        fcast = res.forecast(horizon=5, start=0, reindex=False)
        params = np.asarray(res.params)
        direct = self.ar1.values

        for i in range(5):
            direct = params[0] + params[1] * direct
            assert_allclose(direct, fcast.mean.iloc[:, i])
            scale = np.sum((params[1] ** np.arange(i + 1)) ** 2.0)
            var = fcast.variance.iloc[1:, i]
            assert_allclose(var, scale * params[2] * np.ones_like(var))

        assert np.all(np.asarray(fcast.residual_variance[1:] == params[2]))

        fcast = res.forecast(horizon=5, reindex=False)
        params = np.asarray(res.params)
        assert np.all(np.asarray(np.isnan(fcast.mean[:-1])))
        assert np.all(np.asarray(np.isnan(fcast.variance[:-1])))
        assert np.all(np.asarray(np.isnan(fcast.residual_variance[:-1])))
        assert np.all(np.asarray(fcast.residual_variance.iloc[-1] == params[-1]))
        means = np.zeros(5)
        means[0] = params[0] + params[1] * self.ar1.iloc[-1]
        for i in range(1, 5):
            means[i] = params[0] + params[1] * means[i - 1]
        assert_allclose(means, fcast.mean.iloc[-1].values)

    def test_constant_mean_forecast(self):
        am = arch_model(self.zero_mean, mean="Constant", vol="Constant")
        res = am.fit()
        fcast = res.forecast(horizon=5, reindex=False)

        assert np.all(np.asarray(np.isnan(fcast.mean[:-1])))
        assert np.all(np.asarray(np.isnan(fcast.variance[:-1])))
        assert np.all(np.asarray(np.isnan(fcast.residual_variance[:-1])))
        params = np.asarray(res.params)
        assert_allclose(params[0] * np.ones(5), fcast.mean.iloc[-1])
        assert_allclose(params[1] * np.ones(5), fcast.variance.iloc[-1])
        assert_allclose(params[1] * np.ones(5), fcast.residual_variance.iloc[-1])

        assert fcast.mean.shape == (1, 5)
        assert fcast.variance.shape == (1, 5)
        assert fcast.residual_variance.shape == (1, 5)

    def test_ar2_forecast(self):
        am = arch_model(self.ar2, mean="AR", vol="Constant", lags=[1, 2])
        res = am.fit()

        fcast = res.forecast(horizon=5, reindex=False)
        params = np.asarray(res.params)
        expected = np.zeros(7)
        expected[:2] = self.ar2.iloc[-2:]
        for i in range(2, 7):
            expected[i] = (
                params[0] + params[1] * expected[i - 1] + params[2] * expected[i - 2]
            )

        expected = expected[2:]
        assert np.all(np.asarray(np.isnan(fcast.mean.iloc[:-1])))
        assert_allclose(fcast.mean.iloc[-1], expected)

        expected = np.zeros(5)
        comp = np.array([res.params.iloc[1:3], [1, 0]])
        a = np.eye(2)
        for i in range(5):
            expected[i] = a[0, 0]
            a = a.dot(comp)
        expected = res.params.iloc[-1] * np.cumsum(expected ** 2)
        assert_allclose(fcast.variance.iloc[-1], expected)

        expected = np.empty((1000, 5))
        expected[:2] = np.nan
        expected[2:] = res.params.iloc[-1]

        fcast = res.forecast(horizon=5, start=1, reindex=False)
        expected = np.zeros((999, 7))
        expected[:, 0] = self.ar2.iloc[0:-1]
        expected[:, 1] = self.ar2.iloc[1:]
        for i in range(2, 7):
            expected[:, i] = (
                params[0]
                + params[1] * expected[:, i - 1]
                + params[2] * expected[:, i - 2]
            )
        fill = np.empty((1, 5))
        fill.fill(np.nan)
        expected = np.concatenate((fill, expected[:, 2:]))
        assert_allclose(np.asarray(fcast.mean), expected[1:])

        expected = np.empty((1000, 5))
        expected[:2] = np.nan
        expected[2:] = res.params.iloc[-1]
        assert_allclose(np.asarray(fcast.residual_variance), expected[1:])

        with pytest.raises(ValueError):
            res.forecast(horizon=5, start=0, reindex=False)

    def test_har_forecast(self):
        am = arch_model(self.har3, mean="HAR", vol="Constant", lags=[1, 5, 22])
        res = am.fit()
        fcast_1 = res.forecast(horizon=1, reindex=False)
        fcast_5 = res.forecast(horizon=5, reindex=False)
        assert_allclose(fcast_1.mean, fcast_5.mean.iloc[:, :1])

        with pytest.raises(ValueError):
            res.forecast(horizon=1, start=0, reindex=False)
        with pytest.raises(ValueError):
            res.forecast(horizon=1, start=20, reindex=False)

        fcast_66 = res.forecast(horizon=66, start=21, reindex=False)
        expected = np.empty((1000, 66 + 22))
        expected.fill(np.nan)
        for i in range(22):
            if i < 21:
                expected[21:, i] = self.har3.iloc[i : (-21 + i)]
            else:
                expected[21:, i] = self.har3.iloc[i:]
        params = np.asarray(res.params)
        const = params[0]
        arp = np.zeros(22)
        arp[0] = params[1]
        arp[:5] += params[2] / 5
        arp[:22] += params[3] / 22
        arp_rev = arp[::-1]
        for i in range(22, 88):
            expected[:, i] = const + expected[:, i - 22 : i].dot(arp_rev)
        expected = expected[:, 22:]
        assert_allclose(fcast_66.mean, expected[21:])

        expected[:22] = np.nan
        expected[22:] = res.params.iloc[-1]
        assert_allclose(fcast_66.residual_variance, expected[21:])

        impulse = _ar_to_impulse(66, arp)
        expected = expected * np.cumsum(impulse ** 2)
        assert_allclose(fcast_66.variance, expected[21:])

    def test_forecast_start_alternatives(self):
        am = arch_model(self.har3, mean="HAR", vol="Constant", lags=[1, 5, 22])
        res = am.fit()
        date = self.har3.index[21]
        fcast_1 = res.forecast(start=21, reindex=False)
        fcast_2 = res.forecast(start=date, reindex=False)
        for field in ("mean", "variance", "residual_variance"):
            assert_frame_equal(getattr(fcast_1, field), getattr(fcast_2, field))
        pydt = dt.datetime(date.year, date.month, date.day)
        fcast_2 = res.forecast(start=pydt, reindex=False)
        for field in ("mean", "variance", "residual_variance"):
            assert_frame_equal(getattr(fcast_1, field), getattr(fcast_2, field))

        strdt = pydt.strftime("%Y-%m-%d")
        fcast_2 = res.forecast(start=strdt, reindex=False)
        for field in ("mean", "variance", "residual_variance"):
            assert_frame_equal(getattr(fcast_1, field), getattr(fcast_2, field))

        npydt = np.datetime64(pydt).astype("M8[ns]")
        fcast_2 = res.forecast(start=npydt, reindex=False)
        for field in ("mean", "variance", "residual_variance"):
            assert_frame_equal(getattr(fcast_1, field), getattr(fcast_2, field))

        with pytest.raises(ValueError):
            date = self.har3.index[20]
            res.forecast(start=date, reindex=False)

        with pytest.raises(ValueError):
            date = self.har3.index[0]
            res.forecast(start=date, reindex=False)

        fcast_0 = res.forecast(reindex=False)
        fcast_1 = res.forecast(start=999, reindex=False)
        fcast_2 = res.forecast(start=self.har3.index[999], reindex=False)
        for field in ("mean", "variance", "residual_variance"):
            assert_frame_equal(getattr(fcast_0, field), getattr(fcast_1, field))

            assert_frame_equal(getattr(fcast_0, field), getattr(fcast_2, field))

    def test_fit_options(self):
        am = arch_model(self.zero_mean, mean="Constant", vol="Constant")
        res = am.fit(first_obs=100)
        res.forecast(reindex=False)
        res = am.fit(last_obs=900)
        res.forecast(reindex=False)
        res = am.fit(first_obs=100, last_obs=900)
        res.forecast(reindex=False)
        res.forecast(start=100, reindex=False)
        res.forecast(start=200, reindex=False)
        am = arch_model(self.zero_mean, mean="Constant", vol="Constant", hold_back=20)
        res = am.fit(first_obs=100)
        res.forecast(reindex=False)

    def test_ar1_forecast_simulation(self):
        am = arch_model(self.ar1, mean="AR", vol="GARCH", lags=[1])
        res = am.fit(disp="off")

        with preserved_state(self.rng):
            forecast = res.forecast(
                horizon=5, start=0, method="simulation", reindex=False
            )
            forecast_reindex = res.forecast(
                horizon=5, start=10, method="simulation", reindex=True
            )
        assert forecast.simulations.index.shape[0] == self.ar1.shape[0]
        assert (
            forecast.simulations.index.shape[0] == forecast.simulations.values.shape[0]
        )

        with preserved_state(self.rng):
            forecast_reindex = res.forecast(
                horizon=5, start=10, method="simulation", reindex=True
            )
        assert forecast_reindex.mean.shape[0] == self.ar1.shape[0]
        assert forecast_reindex.simulations.index.shape[0] == self.ar1.shape[0]

        y = np.asarray(self.ar1)
        index = self.ar1.index
        t = y.shape[0]
        params = np.array(res.params)
        resids = np.asarray(y[1:] - params[0] - params[1] * y[:-1])
        vol = am.volatility
        params = np.array(res.params)
        backcast = vol.backcast(resids)
        var_bounds = vol.variance_bounds(resids)
        rng = am.distribution.simulate([])
        vfcast = vol.forecast(
            params[2:],
            resids,
            backcast,
            var_bounds,
            start=0,
            method="simulation",
            rng=rng,
            horizon=5,
        )
        const, ar = params[0], params[1]
        means = np.zeros((t, 5))
        means[:, 0] = const + ar * y
        for i in range(1, 5):
            means[:, i] = const + ar * means[:, i - 1]
        means = pd.DataFrame(
            means, index=index, columns=["h.{0}".format(j) for j in range(1, 6)]
        )
        assert_frame_equal(means, forecast.mean)
        var = np.concatenate([[[np.nan] * 5], vfcast.forecasts])
        rv = pd.DataFrame(
            var, index=index, columns=["h.{0}".format(j) for j in range(1, 6)]
        )
        assert_frame_equal(rv, forecast.residual_variance)

        lrv = rv.copy()
        for i in range(5):
            weights = (ar ** np.arange(i + 1)) ** 2
            weights = weights[:, None]
            lrv.iloc[:, i : i + 1] = rv.values[:, : i + 1].dot(weights[::-1])
        assert_frame_equal(lrv, forecast.variance)

    def test_ar1_forecast_bootstrap(self):
        am = arch_model(self.ar1, mean="AR", vol="GARCH", lags=[1])
        res = am.fit(disp="off")
        rs = np.random.RandomState(98765432)
        state = rs.get_state()
        forecast = res.forecast(
            horizon=5, start=900, method="bootstrap", random_state=rs, reindex=False
        )
        rs.set_state(state)
        repeat = res.forecast(
            horizon=5, start=900, method="bootstrap", random_state=rs, reindex=False
        )
        assert_frame_equal(forecast.mean, repeat.mean)
        assert_frame_equal(forecast.variance, repeat.variance)

    def test_ar2_garch11(self):
        pass

    def test_first_obs(self):
        y = self.ar2_garch
        mod = arch_model(y)
        res = mod.fit(disp="off", first_obs=y.index[100])
        mod = arch_model(y[100:])
        res2 = mod.fit(disp="off")
        assert_allclose(res.params, res2.params)
        mod = arch_model(y)
        res3 = mod.fit(disp="off", first_obs=100)
        assert res.fit_start == 100
        assert_allclose(res.params, res3.params)

        forecast = res.forecast(horizon=3, reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isfinite(forecast.variance)))

        forecast = res.forecast(horizon=3, start=y.index[100], reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isfinite(forecast.variance)))

        forecast = res.forecast(horizon=3, start=100, reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isfinite(forecast.variance)))

        with pytest.raises(ValueError):
            res.forecast(horizon=3, start=y.index[98], reindex=False)

        res = mod.fit(disp="off")
        forecast = res.forecast(horizon=3, reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isfinite(forecast.variance)))

        forecast = res.forecast(horizon=3, start=y.index[100], reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isfinite(forecast.variance)))
        forecast = res.forecast(horizon=3, start=0, reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isfinite(forecast.variance)))

        mod = arch_model(y, mean="AR", lags=[1, 2])
        res = mod.fit(disp="off")
        with pytest.raises(ValueError):
            res.forecast(horizon=3, start=0, reindex=False)

        forecast = res.forecast(horizon=3, start=1, reindex=False)
        assert np.all(np.asarray(np.isfinite(forecast.mean)))
        assert np.all(np.asarray(np.isnan(forecast.variance.iloc[:1])))
        assert np.all(np.asarray(np.isfinite(forecast.variance.iloc[1:])))

    def test_last_obs(self):
        y = self.ar2_garch
        mod = arch_model(y)
        res = mod.fit(disp="off", last_obs=y.index[900])
        res_2 = mod.fit(disp="off", last_obs=900)
        assert_allclose(res.params, res_2.params)
        mod = arch_model(y[:900])
        res_3 = mod.fit(disp="off")
        assert_allclose(res.params, res_3.params)

    def test_first_last_obs(self):
        y = self.ar2_garch
        mod = arch_model(y)
        res = mod.fit(disp="off", first_obs=y.index[100], last_obs=y.index[900])
        res_2 = mod.fit(disp="off", first_obs=100, last_obs=900)
        assert_allclose(res.params, res_2.params)
        mod = arch_model(y.iloc[100:900])
        res_3 = mod.fit(disp="off")
        assert_allclose(res.params, res_3.params)

        mod = arch_model(y)
        res_4 = mod.fit(disp="off", first_obs=100, last_obs=y.index[900])
        assert_allclose(res.params, res_4.params)

    def test_holdback_first_obs(self):
        y = self.ar2_garch
        mod = arch_model(y, hold_back=20)
        res_holdback = mod.fit(disp="off")
        mod = arch_model(y)
        res_first_obs = mod.fit(disp="off", first_obs=20)
        assert_allclose(res_holdback.params, res_first_obs.params)

        with pytest.raises(ValueError):
            res_holdback.forecast(start=18, reindex=False)

    def test_holdback_lastobs(self):
        y = self.ar2_garch
        mod = arch_model(y, hold_back=20)
        res_holdback_last_obs = mod.fit(disp="off", last_obs=800)
        mod = arch_model(y)
        res_first_obs_last_obs = mod.fit(disp="off", first_obs=20, last_obs=800)
        assert_allclose(res_holdback_last_obs.params, res_first_obs_last_obs.params)
        mod = arch_model(y[20:800])
        res_direct = mod.fit(disp="off")
        assert_allclose(res_direct.params, res_first_obs_last_obs.params)

        with pytest.raises(ValueError):
            res_holdback_last_obs.forecast(start=18, reindex=False)

    def test_holdback_ar(self):
        y = self.ar2_garch
        mod = arch_model(y, mean="AR", lags=1, hold_back=1)
        res_holdback = mod.fit(disp="off")
        mod = arch_model(y, mean="AR", lags=1)
        res = mod.fit(disp="off")
        assert_allclose(res_holdback.params, res.params, rtol=1e-4, atol=1e-4)

    def test_forecast_exogenous_regressors(self):
        y = 10 * self.rng.randn(1000, 1)
        x = self.rng.randn(1000, 2)
        am = HARX(y=y, x=x, lags=[1, 2])
        res = am.fit()
        fcasts = res.forecast(horizon=1, start=1, reindex=False)

        const, har01, har02, ex0, ex1, _ = res.params
        y_01 = y[1:-1]
        y_02 = (y[1:-1] + y[0:-2]) / 2
        x0 = x[2:, :1]
        x1 = x[2:, 1:2]
        direct = const + har01 * y_01 + har02 * y_02 + ex0 * x0 + ex1 * x1
        direct = np.vstack(([[np.nan]], direct, [[np.nan]]))
        direct = pd.DataFrame(direct, columns=["h.1"])
        assert_allclose(np.asarray(direct)[1:], fcasts.mean)

        fcasts2 = res.forecast(horizon=2, start=1, reindex=False)
        assert fcasts2.mean.shape == (999, 2)
        assert fcasts2.mean.isnull().all()["h.2"]
        assert_frame_equal(fcasts.mean, fcasts2.mean[["h.1"]])


@pytest.mark.slow
@pytest.mark.parametrize("first_obs", [None, 250])
@pytest.mark.parametrize("last_obs", [None, 2500, 2750])
@pytest.mark.parametrize("reindex", [True, False])
def test_reindex(model_spec, reindex, first_obs, last_obs):
    reindex_dim = SP500.shape[0] - last_obs + 1 if last_obs is not None else 1
    dim0 = SP500.shape[0] if reindex else reindex_dim
    res = model_spec.fit(disp="off", first_obs=first_obs, last_obs=last_obs)
    fcast = res.forecast(horizon=1, reindex=reindex)
    assert fcast.mean.shape == (dim0, 1)
    fcast = res.forecast(
        horizon=2, method="simulation", simulations=25, reindex=reindex
    )
    assert fcast.mean.shape == (dim0, 2)
    fcast = res.forecast(horizon=2, method="bootstrap", simulations=25, reindex=reindex)
    assert fcast.mean.shape == (dim0, 2)
    assert fcast.simulations.values.shape == (dim0, 25, 2)


@pytest.mark.parametrize("reindex", [None, True, False])
def test_reindex_warning(reindex):
    res = arch_model(SP500).fit(disp="off")
    warning = FutureWarning if reindex is None else None
    match = "The default for reindex" if reindex is None else None
    with pytest.warns(warning, match=match):
        res.forecast(reindex=reindex)


def test_reindex_future_import():
    res = arch_model(SP500).fit(disp="off")
    with pytest.warns(FutureWarning, match="The default for reindex"):
        default = res.forecast()
    with pytest.warns(None):
        fcast = res.forecast(reindex=False)

    from arch.__future__ import reindexing  # noqa: F401

    future_name = "arch.__future__.reindexing"
    assert future_name in sys.modules
    with pytest.warns(None):
        post = res.forecast()
    assert post.mean.shape == fcast.mean.shape
    assert post.mean.shape != default.mean.shape

    del sys.modules[future_name]
    assert future_name not in sys.modules
    with pytest.warns(FutureWarning, match="The default for reindex"):
        res.forecast()


def test_invalid_horizon():
    res = arch_model(SP500).fit(disp="off")
    with pytest.raises(ValueError, match="horizon must be"):
        res.forecast(horizon=-1, reindex=False)
    with pytest.raises(ValueError, match="horizon must be"):
        res.forecast(horizon=1.0, reindex=False)
    with pytest.raises(ValueError, match="horizon must be"):
        res.forecast(horizon="5", reindex=False)


def test_arx_no_lags():
    mod = ARX(SP500, volatility=GARCH())
    res = mod.fit(disp="off")
    assert res.params.shape[0] == 4
    assert "lags" not in mod._model_description(include_lags=False)
