# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import absolute_import, division, print_function, unicode_literals
import astropy.units as u
from gammapy.utils.energy import EnergyBounds
from astropy.tests.helper import assert_quantity_allclose, pytest
from ..models import (PowerLaw, PowerLaw2, ExponentialCutoffPowerLaw,
                      ExponentialCutoffPowerLaw3FGL, LogParabola,
                      TableModel, AbsorbedSpectralModel)
from ...utils.testing import requires_dependency, requires_data


def table_model():
    energy_edges = EnergyBounds.equal_log_spacing(0.1 * u.TeV, 100 * u.TeV, 1000)
    energy = energy_edges.log_centers

    index = 2.3 * u.Unit('')
    amplitude = 4 / u.cm ** 2 / u.s / u.TeV
    reference = 1 * u.TeV
    pl = PowerLaw(index, amplitude, reference)
    flux = pl(energy)

    return TableModel(energy, flux, 1 * u.Unit(''))


TEST_MODELS = [
    dict(
        name='powerlaw',
        model=PowerLaw(
            index=2.3 * u.Unit(''),
            amplitude=4 / u.cm ** 2 / u.s / u.TeV,
            reference=1 * u.TeV
        ),
        val_at_2TeV=u.Quantity(4 * 2. ** (-2.3), 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(2.9227116204223784, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(6.650836884969039, 'TeV cm-2 s-1'),
    ),

    dict(
        name='powerlaw',
        model=PowerLaw(
            index=2 * u.Unit(''),
            amplitude=4 / u.cm ** 2 / u.s / u.TeV,
            reference=1 * u.TeV
        ),
        val_at_2TeV=u.Quantity(1.0, 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(3.6, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(9.210340371976184, 'TeV cm-2 s-1'),
    ),

    dict(
        name='powerlaw2',
        model=PowerLaw2(
            amplitude=u.Quantity(2.9227116204223784, 'cm-2 s-1'),
            index=2.3 * u.Unit(''),
            emin=1 * u.TeV,
            emax=10 * u.TeV
        ),
        val_at_2TeV=u.Quantity(4 * 2. ** (-2.3), 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(2.9227116204223784, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(6.650836884969039, 'TeV cm-2 s-1'),
    ),

    dict(
        name='ecpl',
        model=ExponentialCutoffPowerLaw(
            index=2.3 * u.Unit(''),
            amplitude=4 / u.cm ** 2 / u.s / u.TeV,
            reference=1 * u.TeV,
            lambda_=0.1 / u.TeV
        ),

        val_at_2TeV=u.Quantity(0.6650160161581361, 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(2.3556579120286796, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(4.83209019773561, 'TeV cm-2 s-1'),
    ),

    dict(
        name='ecpl_3fgl',
        model=ExponentialCutoffPowerLaw3FGL(
            index=2.3 * u.Unit(''),
            amplitude=4 / u.cm ** 2 / u.s / u.TeV,
            reference=1 * u.TeV,
            ecut=10 * u.TeV
        ),

        val_at_2TeV=u.Quantity(0.7349563611124971, 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(2.6034046173089, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(5.340285560055799, 'TeV cm-2 s-1'),
    ),

    dict(
        name='logpar',
        model=LogParabola(
            alpha=2.3 * u.Unit(''),
            amplitude=4 / u.cm ** 2 / u.s / u.TeV,
            reference=1 * u.TeV,
            beta=0.5 * u.Unit('')
        ),

        val_at_2TeV=u.Quantity(0.6387956571420305, 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(2.255689748270628, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(3.9586515834989267, 'TeV cm-2 s-1')
    ),
]

# The table model imports scipy.interpolate in `__init__`,
# so we skip it if scipy is not available
try:
    TEST_MODELS.append(dict(
        name='table_model',
        model=table_model(),
        # Values took from power law expectation
        val_at_2TeV=u.Quantity(4 * 2. ** (-2.3), 'cm-2 s-1 TeV-1'),
        integral_1_10TeV=u.Quantity(2.9227116204223784, 'cm-2 s-1'),
        eflux_1_10TeV=u.Quantity(6.650836884969039, 'TeV cm-2 s-1'),
    ))
except ImportError:
    pass


@requires_dependency('uncertainties')
@pytest.mark.parametrize(
    "spectrum", TEST_MODELS, ids=[_['name'] for _ in TEST_MODELS]
)
def test_models(spectrum):
    model = spectrum['model']
    energy = 2 * u.TeV
    value = model(energy)
    assert_quantity_allclose(value, spectrum['val_at_2TeV'])
    emin = 1 * u.TeV
    emax = 10 * u.TeV
    assert_quantity_allclose(model.integral(emin=emin, emax=emax),
                             spectrum['integral_1_10TeV'])
    assert_quantity_allclose(model.energy_flux(emin=emin, emax=emax),
                             spectrum['eflux_1_10TeV'])

    # inverse for TableModel is not implemented
    if not isinstance(model, TableModel):
        assert_quantity_allclose(model.inverse(value), 2 * u.TeV, rtol=0.05)
    model.to_dict()


@requires_dependency('matplotlib')
@requires_dependency('sherpa')
@pytest.mark.parametrize(
    "spectrum", TEST_MODELS, ids=[_['name'] for _ in TEST_MODELS]
)
def test_to_sherpa(spectrum):
    model = spectrum['model']
    try:
        sherpa_model = model.to_sherpa()
    except NotImplementedError:
        pass
    else:
        test_e = 1.56 * u.TeV
        desired = model(test_e)
        actual = sherpa_model(test_e.to('keV').value) * u.Unit('cm-2 s-1 keV-1')
        assert_quantity_allclose(actual, desired)

    # test plot
    energy_range = [1, 10] * u.TeV
    model.plot(energy_range=energy_range)


@requires_dependency('matplotlib')
@requires_data('gammapy-extra')
def test_table_model_from_file():
    filename = '$GAMMAPY_EXTRA/datasets/ebl/ebl_franceschini.fits.gz'
    absorption_z03 = TableModel.read_xspec_model(filename=filename, param=0.3)
    absorption_z03.plot(energy_range=(0.03, 10),
                        energy_unit=u.TeV)


@requires_dependency('scipy')
@requires_data('gammapy-extra')
def test_absorbed_spectral_model():
    filename = '$GAMMAPY_EXTRA/datasets/ebl/ebl_franceschini.fits.gz'
    # absorption values for given redshift
    redshift = 0.117
    absorption = TableModel.read_xspec_model(filename, redshift)
    # Spectral model corresponding to PKS 2155-304 (quiescent state)
    index = 3.53
    amplitude = 1.81 * 1e-12 * u.Unit('cm-2 s-1 TeV-1')
    reference = 1 * u.TeV
    pwl = PowerLaw(index=index, amplitude=amplitude, reference=reference)
    # EBL + PWL model
    model = AbsorbedSpectralModel(spectral_model=pwl, table_model=absorption)

    # Test if the absorption factor at the reference energy
    # corresponds to the ratio of the absorbed flux
    # divided by the flux of the spectral model
    model_ref_energy = model.evaluate(energy=reference)
    pwl_ref_energy = pwl.evaluate(energy=reference,
                                  index=index,
                                  amplitude=amplitude,
                                  reference=reference)

    desired = absorption.evaluate(energy=reference, amplitude=1.)
    actual = model_ref_energy/pwl_ref_energy
    assert_quantity_allclose(actual, desired)

@requires_dependency('uncertainties')
def test_pwl_index_2_error():
    pars, errs = {}, {}
    pars['amplitude'] = 1E-12 * u.Unit('TeV-1 cm-2 s-1')
    pars['reference'] = 1 * u.Unit('TeV')
    pars['index'] = 2 * u.Unit('')
    errs['amplitude'] = 0.1E-12 * u.Unit('TeV-1 cm-2 s-1')

    pwl = PowerLaw(**pars)
    pwl.parameters.set_parameter_errors(errs)

    val, val_err = pwl.evaluate_error(1 * u.TeV)
    assert_quantity_allclose(val, 1E-12 * u.Unit('TeV-1 cm-2 s-1'))
    assert_quantity_allclose(val_err, 0.1E-12 * u.Unit('TeV-1 cm-2 s-1'))

    flux, flux_err = pwl.integral_error(1 * u.TeV, 10 * u.TeV)
    assert_quantity_allclose(flux, 9E-13 * u.Unit('cm-2 s-1'))
    assert_quantity_allclose(flux_err, 9E-14 * u.Unit('cm-2 s-1'))

    eflux, eflux_err = pwl.energy_flux_error(1 * u.TeV, 10 * u.TeV)
    assert_quantity_allclose(eflux, 2.302585E-12 * u.Unit('TeV cm-2 s-1'))
    assert_quantity_allclose(eflux_err, 0.2302585E-12 * u.Unit('TeV cm-2 s-1'))


@requires_dependency('sherpa')
@requires_data('gammapy-extra')
def test_spectral_model_absorbed_by_ebl():
    from gammapy.spectrum.models import SpectralModelAbsorbedbyEbl
    from gammapy.scripts import CTAPerf
    from gammapy.scripts.cta_utils import CTAObservationSimulation, Target, ObservationParameters
    from gammapy.spectrum import SpectrumObservationList, SpectrumFit, SpectrumResult

    # Observation parameters
    obs_param = ObservationParameters(alpha=0.2 * u.Unit(''),
                                      livetime=5. * u.h,
                                      emin=0.08 * u.TeV,
                                      emax=12 * u.TeV)

    # Target, PKS 2155-304 from 3FHL
    name = 'test'
    pwl = PowerLaw(index=3. * u.Unit(''),
                   amplitude=1.e-12 * u.Unit('1/(cm2 s TeV)'),
                   reference=1. * u.TeV)

    # here is the new model
    input_model = SpectralModelAbsorbedbyEbl(spectral_model=pwl,
                                             redshift=0.1,
                                             ebl_model='$GAMMAPY_EXTRA/datasets/ebl/ebl_dominguez11.fits.gz')

    # should be changed, I hack to avoid double absorption by EBL
    # (redshift = None)
    target = Target(name=name, model=input_model,
                    redshift=None,
                    ebl_model_name='dominguez')
    
    # Performance
    filename = '$GAMMAPY_EXTRA/datasets/cta/perf_prod2/point_like_non_smoothed/South_5h.fits.gz'
    cta_perf = CTAPerf.read(filename)

    # Simulation
    simu = CTAObservationSimulation.simulate_obs(perf=cta_perf,
                                                 target=target,
                                                 obs_param=obs_param)

    # Model we want to fit
    model = SpectralModelAbsorbedbyEbl(spectral_model=PowerLaw(index=2.5 * u.Unit(''),
                                                               amplitude=1.e-12 * u.Unit('1/(cm2 s TeV)'),
                                                               reference=1. * u.TeV),
                                       redshift=0.1,
                                       ebl_model='$GAMMAPY_EXTRA/datasets/ebl/ebl_dominguez11.fits.gz')

    # fit
    fit = SpectrumFit(obs_list=SpectrumObservationList([simu]),
                      model=model,
                      stat='wstat')
    fit.fit()
    fit.est_errors()
    result = fit.result[0]

    # here it explodes since NDDataArray can't handle error propagation
    butterfly = result.butterfly()
