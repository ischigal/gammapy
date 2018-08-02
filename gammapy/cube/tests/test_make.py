# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import absolute_import, division, print_function, unicode_literals
import pytest
from numpy.testing import assert_allclose
import astropy.units as u
from astropy.coordinates import SkyCoord
from ...utils.testing import requires_data
from ...data import DataStore
from ...maps import WcsGeom, MapAxis
from ..make import MapMaker

pytest.importorskip('scipy')


@pytest.fixture(scope='session')
def obs_list():
    data_store = DataStore.from_dir("$GAMMAPY_EXTRA/datasets/cta-1dc/index/gps/")
    obs_id = [110380, 111140]
    return data_store.obs_list(obs_id)


def geom(ebounds):
    skydir = SkyCoord(0, -1, unit="deg", frame='galactic')
    energy_axis = MapAxis.from_edges(ebounds, name='energy', unit='TeV', interp='log')
    return WcsGeom.create(binsz=0.5 * u.deg, skydir=skydir, width=(10, 5),
                          coordsys='GAL', axes=[energy_axis])


@requires_data('gammapy-extra')
@pytest.mark.parametrize("pars", [
    {
        # Default, normal test case
        'geom': geom(ebounds=[0.1, 1, 10]),
        'mode': 'trim',
        'counts': 34366,
        'exposure': 3.99815e+11,
        'background': 34366,
    },
    {
        # Test single energy bin
        'geom': geom(ebounds=[0.1, 10]),
        'mode': 'trim',
        'counts': 34366,
        'exposure': 1.16866e+11,
        'background': 34366,
    },
    {
        # Test strict mode
        'geom': geom(ebounds=[0.1, 1, 10]),
        'mode': 'strict',
        'counts': 21981,
        'exposure': 2.592941e+11,
        'background': 21981,
    },
])
def test_map_maker(pars, obs_list):
    maker = MapMaker(
        geom=pars['geom'],
        offset_max='2 deg',
        cutout_mode=pars['mode'],
    )
    maps = maker.run(obs_list)

    counts = maps['counts']
    assert counts.unit == ""
    assert_allclose(counts.data.sum(), pars['counts'], rtol=1e-5)

    exposure = maps['exposure']
    assert exposure.unit == "m2 s"
    assert_allclose(exposure.data.sum(), pars['exposure'], rtol=1e-5)

    background = maps['background']
    assert background.unit == ""
    assert_allclose(background.data.sum(), pars['background'], rtol=1e-5)