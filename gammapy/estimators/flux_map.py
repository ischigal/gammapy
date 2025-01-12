# Licensed under a 3-clause BSD style license - see LICENSE.rst
import logging
import numpy as np
from astropy import units as u
from astropy.io import fits
from astropy.table import Table
from astropy.utils import classproperty
from gammapy.data import GTI
from gammapy.maps import Map, Maps
from gammapy.modeling.models import Models, SkyModel, PowerLawSpectralModel, SpectralModel
from gammapy.utils.scripts import make_path

__all__ = ["FluxMaps"]

log = logging.getLogger(__name__)


DEFAULT_UNIT = {
    "dnde": u.Unit("cm-2 s-1 TeV-1"),
    "e2dnde": u.Unit("erg cm-2 s-1"),
    "flux": u.Unit("cm-2 s-1"),
    "eflux": u.Unit("erg cm-2 s-1"),
    "norm": u.Unit("")
}

REQUIRED_MAPS = {
    "dnde": ["dnde"],
    "e2dnde": ["e2dnde"],
    "flux": ["flux"],
    "eflux": ["eflux"],
    "likelihood": ["norm"],
}

REQUIRED_COLUMNS = {
    "dnde": ["e_ref", "dnde"],
    "e2dnde": ["e_ref", "e2dnde"],
    "flux": ["e_min", "e_max", "flux"],
    "eflux": ["e_min", "e_max", "eflux"],
    # TODO: extend required columns
    "likelihood": ["e_min", "e_max", "e_ref", "ref_dnde", "ref_flux", "ref_eflux", "norm"],
}


REQUIRED_QUANTITIES_SCAN = ["stat_scan", "stat"]

OPTIONAL_QUANTITIES = {
    "dnde": ["dnde_err", "dnde_errp", "dnde_errn", "dnde_ul"],
    "e2dnde": ["e2dnde_err", "e2dnde_errp", "e2dnde_errn", "e2dnde_ul"],
    "flux": ["flux_err", "flux_errp", "flux_errn", "flux_ul"],
    "eflux": ["eflux_err", "eflux_errp", "eflux_errn", "eflux_ul"],
    "likelihood": ["norm_err", "norm_errn", "norm_errp", "norm_ul"],
}

VALID_QUANTITIES = [
    "norm",
    "norm_err",
    "norm_errn",
    "norm_errp",
    "norm_ul",
    "ts",
    "sqrt_ts",
    "npred",
    "npred_excess",
    "npred_null",
    "stat",
    "stat_scan",
    "stat_null",
    "niter",
    "is_ul",
    "counts"
]


OPTIONAL_QUANTITIES_COMMON = [
    "ts",
    "sqrt_ts",
    "npred",
    "npred_excess",
    "npred_null",
    "stat",
    "stat_null",
    "niter",
#    "is_ul",
    "counts"
]


class FluxMaps:
    """A flux map / points container.

    It contains a set of `~gammapy.maps.Map` objects that store the estimated
    flux as a function of energy as well as associated quantities (typically
    errors, upper limits, delta TS and possibly raw quantities such counts,
    excesses etc). It also contains a reference model to convert the flux
    values in different formats. Usually, this should be the model used to
    produce the flux map.

    The associated map geometry can use a `RegionGeom` to store the equivalent
    of flux points, or a `WcsGeom`/`HpxGeom` to store an energy dependent flux map.

    The container relies internally on the 'Likelihood' SED type defined in
    :ref:`gadf:flux-points` and offers convenience properties to convert to
    other flux formats, namely: ``dnde``, ``flux``, ``eflux`` or ``e2dnde``.
    The conversion is done according to the reference model spectral shape.

    Parameters
    ----------
    data : dict of `~gammapy.maps.Map`
        the maps dictionary. Expected entries are the following:
        * norm : the norm factor
        * norm_err : optional, the error on the norm factor.
        * norm_errn : optional, the negative error on the norm factor.
        * norm_errp : optional, the positive error on the norm factor.
        * norm_ul : optional, the upper limit on the norm factor.
        * norm_scan : optional, the norm values of the test statistic scan.
        * stat_scan : optional, the test statistic scan values.
        * ts : optional, the delta TS associated with the flux value.
        * sqrt_ts : optional, the square root of the TS, when relevant.
    reference_model : `~gammapy.modeling.models.SkyModel`, optional
        the reference model to use for conversions. Default in None.
        If None, a model consisting of a point source with a power law spectrum of index 2 is assumed.
    gti : `~gammapy.data.GTI`
        the maps GTI information. Default is None.
    """
    _expand_slice = (slice(None), np.newaxis, np.newaxis)

    def __init__(self, data, reference_model, meta=None, gti=None):
        self._data = data

        if isinstance(reference_model, SpectralModel):
            reference_model = SkyModel(reference_model)

        self._reference_model = reference_model

        if meta is None:
            meta = {}

        self.meta = meta
        self.gti = gti

    @property
    def available_quantities(self):
        """Available quantities"""
        return list(self._data.keys())

    @staticmethod
    def all_quantities(sed_type):
        """All quantities quantities"""
        quantities = []
        quantities += REQUIRED_MAPS[sed_type]
        quantities += OPTIONAL_QUANTITIES[sed_type]
        quantities += OPTIONAL_QUANTITIES_COMMON

        if sed_type == "likelihood":
            quantities += REQUIRED_QUANTITIES_SCAN

        return quantities

    @staticmethod
    def _validate_data(data, sed_type, check_scan=False):
        """Check that map input is valid and correspond to one of the SED type."""
        try:
            keys = data.keys()
            required = set(REQUIRED_MAPS[sed_type])
        except KeyError:
            raise ValueError(f"Unknown SED type: '{sed_type}'")

        if check_scan:
            required = required.union(REQUIRED_QUANTITIES_SCAN)

        if not required.issubset(keys):
            missing = required.difference(keys)
            raise ValueError(
                "Missing data / column for sed type '{}':" " {}".format(sed_type, missing)
            )

    # TODO: add support for scan
    def _check_quantity(self, quantity):
        if quantity not in self.available_quantities:
            raise AttributeError(
                f"Quantity '{quantity}' is not defined on current flux estimate."
            )

    @staticmethod
    def _guess_sed_type(quantities):
        """Guess SED type from table content."""
        valid_sed_types = list(REQUIRED_COLUMNS.keys())
        for sed_type in valid_sed_types:
            required = set(REQUIRED_COLUMNS[sed_type])
            if required.issubset(quantities):
                return sed_type

    @property
    def has_stat_profiles(self):
        """Whether the fluc estimate has stat profiles"""
        return "stat_scan" in self._data

    @property
    def n_sigma(self):
        """n sigma UL"""
        return self.meta.get("n_sigma", 1)

    @property
    def n_sigma_ul(self):
        """n sigma UL"""
        return self.meta.get("n_sigma_ul")

    @property
    def ts_threshold_ul(self):
        """TS threshold for upper limits"""
        return self.meta.get("ts_threshold_ul", 4)

    @property
    def sed_type_init(self):
        """Initial sed type"""
        return self.meta.get("sed_type_init")

    @property
    def geom(self):
        """Reference map geometry (`Geom`)"""
        return self.norm.geom

    @property
    def energy_axis(self):
        """Energy axis (`MapAxis`)"""
        return self.geom.axes["energy"]

    @classproperty
    def reference_model_default(self):
        """Reference model default (`SkyModel`) """
        return SkyModel(PowerLawSpectralModel(index=2))

    @property
    def reference_model(self):
        """Reference model (`SkyModel`)"""
        return self._reference_model

    @property
    def reference_spectral_model(self):
        """Reference spectral model (`SpectralModel`)"""
        return self.reference_model.spectral_model

    @property
    def energy_ref(self):
        """Reference energy.

        Defined by `energy_ref` column in `FluxPoints.table` or computed as log
        center, if `energy_min` and `energy_max` columns are present in `FluxEstimate.data`.

        Returns
        -------
        energy_ref : `~astropy.units.Quantity`
            Reference energy.
        """
        return self.energy_axis.center

    @property
    def energy_min(self):
        """Energy min

        Returns
        -------
        energy_min : `~astropy.units.Quantity`
            Lower bound of energy bin.
        """
        return self.energy_axis.edges[:-1]

    @property
    def energy_max(self):
        """Energy max

        Returns
        -------
        energy_max : `~astropy.units.Quantity`
            Upper bound of energy bin.
        """
        return self.energy_axis.edges[1:]

    # TODO: keep or remove?
    @property
    def niter(self):
        """Number of iterations of fit"""
        self._check_quantity("niter")
        return self._data["niter"]

    @property
    def is_ul(self):
        """Whether data is an upper limit"""
        # TODO: make this a well defined behaviour
        is_ul = self.norm.copy()

        if "ts" in self._data and "norm_ul" in self._data:
            is_ul.data = self.ts.data < self.ts_threshold_ul
        elif "norm_ul" in self._data:
            is_ul.data = np.isfinite(self.norm_ul)
        else:
            is_ul.data = np.isnan(self.norm)

        return is_ul

    @property
    def counts(self):
        """Predicted counts null hypothesis"""
        self._check_quantity("counts")
        return self._data["counts"]

    @property
    def npred(self):
        """Predicted counts"""
        self._check_quantity("npred")
        return self._data["npred"]

    @property
    def npred_null(self):
        """Predicted counts null hypothesis"""
        self._check_quantity("npred_null")
        return self._data["npred_null"]

    @property
    def npred_excess(self):
        """Predicted excess counts"""
        self._check_quantity("npred")
        self._check_quantity("npred_null")
        return self._data["npred"] - self._data["npred_null"]

    @property
    def stat_scan(self):
        """Fit statistic value"""
        self._check_quantity("stat_scan")
        return self._data["stat_scan"]

    @property
    def stat(self):
        """Fit statistic value"""
        self._check_quantity("stat")
        return self._data["stat"]

    @property
    def stat_null(self):
        """Fit statistic value for the null hypothesis"""
        self._check_quantity("stat_null")
        return self._data["stat_null"]

    @property
    def ts(self):
        """ts map (`Map`)"""
        self._check_quantity("ts")
        return self._data["ts"]

    # TODO: always derive sqrt(TS) from TS?
    @property
    def sqrt_ts(self):
        """sqrt(TS) as defined by:

        .. math::

            \sqrt{TS} = \left \{
            \begin{array}{ll}
              -\sqrt{TS} & : \text{if} \ norm < 0 \\
              \sqrt{TS} & : \text{else}
            \end{array}
            \right.

        Returns
        -------
        sqrt_ts : `Map`
            sqrt(TS) map
        """
        if "sqrt_ts" in self._data:
            return self._data["sqrt_ts"]
        else:
            with np.errstate(invalid="ignore", divide="ignore"):
                data = np.where(self.norm > 0, np.sqrt(self.ts), -np.sqrt(self.ts))
                return Map.from_geom(geom=self.geom, data=data)

    @property
    def norm(self):
        """Norm values"""
        return self._data["norm"]

    @property
    def norm_err(self):
        """Norm error"""
        self._check_quantity("norm_err")
        return self._data["norm_err"]

    @property
    def norm_errn(self):
        """Negative norm error"""
        self._check_quantity("norm_errn")
        return self._data["norm_errn"]

    @property
    def norm_errp(self):
        """Positive norm error"""
        self._check_quantity("norm_errp")
        return self._data["norm_errp"]

    @property
    def norm_ul(self):
        """Norm upper limit"""
        self._check_quantity("norm_ul")
        return self._data["norm_ul"]

    @property
    def dnde_ref(self):
        """Reference differential flux"""
        result = self.reference_spectral_model(self.energy_axis.center)
        return result[self._expand_slice]

    @property
    def e2dnde_ref(self):
        """Reference differential flux * energy ** 2"""
        energy = self.energy_axis.center
        result = (
                self.reference_spectral_model(energy) * energy ** 2
        )
        return result[self._expand_slice]

    @property
    def flux_ref(self):
        """Reference integral flux"""
        energy_min = self.energy_axis.edges[:-1]
        energy_max = self.energy_axis.edges[1:]
        result = self.reference_spectral_model.integral(energy_min, energy_max)
        return result[self._expand_slice]

    @property
    def eflux_ref(self):
        """Reference energy flux"""
        energy_min = self.energy_axis.edges[:-1]
        energy_max = self.energy_axis.edges[1:]
        result = self.reference_spectral_model.energy_flux(energy_min, energy_max)
        return result[self._expand_slice]

    @property
    def dnde(self):
        """Return differential flux (dnde) SED values."""
        return self.norm * self.dnde_ref

    @property
    def dnde_err(self):
        """Return differential flux (dnde) SED errors."""
        return self.norm_err * self.dnde_ref

    @property
    def dnde_errn(self):
        """Return differential flux (dnde) SED negative errors."""
        return self.norm_errn * self.dnde_ref

    @property
    def dnde_errp(self):
        """Return differential flux (dnde) SED positive errors."""
        return self.norm_errp * self.dnde_ref

    @property
    def dnde_ul(self):
        """Return differential flux (dnde) SED upper limit."""
        return self.norm_ul * self.dnde_ref

    @property
    def e2dnde(self):
        """Return differential energy flux (e2dnde) SED values."""
        return self.norm * self.e2dnde_ref

    @property
    def e2dnde_err(self):
        """Return differential energy flux (e2dnde) SED errors."""
        return self.norm_err * self.e2dnde_ref

    @property
    def e2dnde_errn(self):
        """Return differential energy flux (e2dnde) SED negative errors."""
        return self.norm_errn * self.e2dnde_ref

    @property
    def e2dnde_errp(self):
        """Return differential energy flux (e2dnde) SED positive errors."""
        return self.norm_errp * self.e2dnde_ref

    @property
    def e2dnde_ul(self):
        """Return differential energy flux (e2dnde) SED upper limit."""
        return self.norm_ul * self.e2dnde_ref

    @property
    def flux(self):
        """Return integral flux (flux) SED values."""
        return self.norm * self.flux_ref

    @property
    def flux_err(self):
        """Return integral flux (flux) SED values."""
        return self.norm_err * self.flux_ref

    @property
    def flux_errn(self):
        """Return integral flux (flux) SED negative errors."""
        return self.norm_errn * self.flux_ref

    @property
    def flux_errp(self):
        """Return integral flux (flux) SED positive errors."""
        return self.norm_errp * self.flux_ref

    @property
    def flux_ul(self):
        """Return integral flux (flux) SED upper limits."""
        return self.norm_ul * self.flux_ref

    @property
    def eflux(self):
        """Return energy flux (eflux) SED values."""
        return self.norm * self.eflux_ref

    @property
    def eflux_err(self):
        """Return energy flux (eflux) SED errors."""
        return self.norm_err * self.eflux_ref

    @property
    def eflux_errn(self):
        """Return energy flux (eflux) SED negative errors."""
        return self.norm_errn * self.eflux_ref

    @property
    def eflux_errp(self):
        """Return energy flux (eflux) SED positive errors."""
        return self.norm_errp * self.eflux_ref

    @property
    def eflux_ul(self):
        """Return energy flux (eflux) SED upper limits."""
        return self.norm_ul * self.eflux_ref

    def get_flux_points(self, position=None):
        """Extract flux point at a given position.

        Parameters
        ---------
        position : `~astropy.coordinates.SkyCoord`
            Position where the flux points are extracted.

        Returns
        -------
        flux_points : `~gammapy.estimators.FluxPoints`
            Flux points object
        """
        from gammapy.estimators import FluxPoints

        if position is None:
            position = self.geom.center_skydir

        data = {}

        for name in self._data:
            m = getattr(self, name)
            data[name] = m.to_region_nd_map(region=position, method="nearest")

        return FluxPoints(
            data,
            reference_model=self.reference_model,
            meta=self.meta.copy(),
            gti=self.gti
        )

    def to_maps(self, sed_type="likelihood"):
        """Return maps in a given SED type.

        Parameters
        ----------
        sed_type : str
            sed type to convert to. Default is `Likelihood`

        Returns
        -------
        maps : `Maps`
            Maps object containing the requested maps.
        """
        maps = Maps()

        for quantity in self.all_quantities(sed_type=sed_type):
            m = getattr(self, quantity, None)
            if m is not None:
                maps[quantity] = m

        return maps

    @classmethod
    def from_maps(cls, maps, sed_type=None, reference_model=None, gti=None, meta=None):
        """Create FluxMaps from a dictionary of maps.

        Parameters
        ----------
        maps : `Maps`
            Maps object containing the input maps.
        sed_type : str
            SED type of the input maps. Default is `Likelihood`
        reference_model : `~gammapy.modeling.models.SkyModel`, optional
            Reference model to use for conversions. Default in None.
            If None, a model consisting of a point source with a power
            law spectrum of index 2 is assumed.
        gti : `~gammapy.data.GTI`
            Maps GTI information. Default is None.
        meta : `dict`
            Meta dict.

        Returns
        -------
        flux_maps : `~gammapy.estimators.FluxMaps`
            Flux maps object.
        """
        if sed_type is None:
            sed_type = cls._guess_sed_type(maps.keys())

        if sed_type is None:
            raise ValueError("Specifying the sed type is required")

        cls._validate_data(data=maps, sed_type=sed_type)

        if sed_type == "likelihood":
            return cls(data=maps, reference_model=reference_model, gti=gti, meta=meta)

        if reference_model is None:
            log.warning(
                "No reference model set for FluxMaps. Assuming point source with E^-2 spectrum."
            )
            reference_model = cls.reference_model_default
        elif isinstance(reference_model, SpectralModel):
            reference_model = SkyModel(reference_model)

        map_ref = maps[sed_type]
        energy_axis = map_ref.geom.axes["energy"]

        with np.errstate(invalid="ignore", divide="ignore"):
            fluxes = reference_model.spectral_model.reference_fluxes(energy_axis=energy_axis)

        # TODO: handle reshaping in MapAxis
        factor = fluxes[f"ref_{sed_type}"].to(map_ref.unit)[cls._expand_slice]

        data = dict()
        data["norm"] = map_ref / factor

        for key in OPTIONAL_QUANTITIES[sed_type]:
            if key in maps:
                norm_type = key.replace(sed_type, "norm")
                data[norm_type] = maps[key] / factor

        # We add the remaining maps
        for key in OPTIONAL_QUANTITIES_COMMON:
            if key in maps:
                data[key] = maps[key]

        return cls(data=data, reference_model=reference_model, gti=gti, meta=meta)

    def to_hdulist(self, sed_type="likelihood", hdu_bands=None):
        """Convert flux map to list of HDUs.

        For now, one cannot export the reference model.

        Parameters
        ----------
        sed_type : str
            sed type to convert to. Default is `Likelihood`
        hdu_bands : str
            Name of the HDU with the BANDS table. Default is 'BANDS'
            If set to None, each map will have its own hdu_band

        Returns
        -------
        hdulist : `~astropy.io.fits.HDUList`
            Map dataset list of HDUs.
        """
        exclude_primary = slice(1, None)

        hdu_primary = fits.PrimaryHDU()
        hdu_primary.header["SED_TYPE"] = sed_type
        hdulist = fits.HDUList([hdu_primary])

        maps = self.to_maps(sed_type=sed_type)
        hdulist.extend(maps.to_hdulist(hdu_bands=hdu_bands)[exclude_primary])

        if self.gti:
            hdu = fits.BinTableHDU(self.gti.table, name="GTI")
            hdulist.append(hdu)

        return hdulist

    @classmethod
    def from_hdulist(cls, hdulist, hdu_bands=None, sed_type=None):
        """Create flux map dataset from list of HDUs.

        Parameters
        ----------
        hdulist : `~astropy.io.fits.HDUList`
            List of HDUs.
        hdu_bands : str
            Name of the HDU with the BANDS table. Default is 'BANDS'
            If set to None, each map should have its own hdu_band
        sed_type : {"dnde", "flux", "e2dnde", "eflux", "likelihood"}
            Sed type

        Returns
        -------
        flux_maps : `~gammapy.estimators.FluxMaps`
            Flux maps object.
        """
        maps = Maps.from_hdulist(hdulist=hdulist, hdu_bands=hdu_bands)

        if sed_type is None:
            sed_type = hdulist[0].header.get("SED_TYPE", None)

        filename = hdulist[0].header.get("MODEL", None)

        if filename:
            reference_model = Models.read(filename)[0]
        else:
            reference_model = None

        if "GTI" in hdulist:
            gti = GTI(Table.read(hdulist["GTI"]))
        else:
            gti = None

        return cls.from_maps(
            maps=maps,
            sed_type=sed_type,
            reference_model=reference_model,
            gti=gti
        )

    def write(
            self, filename, filename_model=None, overwrite=False, sed_type="likelihood"
    ):
        """Write flux map to file.

        Parameters
        ----------
        filename : str
            Filename to write to.
        filename_model : str
            Filename of the model (yaml format).
            If None, keep string before '.' and add '_model.yaml' suffix
        overwrite : bool
            Overwrite file if it exists.
        sed_type : str
            sed type to convert to. Default is `likelihood`
        """
        filename = make_path(filename)

        if filename_model is None:
            name_string = filename.as_posix()
            for suffix in filename.suffixes:
                name_string.replace(suffix, "")
            filename_model = name_string + "_model.yaml"

        filename_model = make_path(filename_model)

        hdulist = self.to_hdulist(sed_type)

        models = Models(self.reference_model)
        models.write(filename_model, overwrite=overwrite)
        hdulist[0].header["MODEL"] = filename_model.as_posix()

        hdulist.writeto(filename, overwrite=overwrite)

    @classmethod
    def read(cls, filename):
        """Read map dataset from file.

        Parameters
        ----------
        filename : str
            Filename to read from.

        Returns
        -------
        flux_maps : `~gammapy.estimators.FluxMaps`
            Flux maps object.
        """
        with fits.open(str(make_path(filename)), memmap=False) as hdulist:
            return cls.from_hdulist(hdulist)

    # TODO: should we allow this?
    def __getitem__(self, item):
        return getattr(self, item)

    def __str__(self):
        str_ = f"{self.__class__.__name__}\n"
        str_ += "-" * len(self.__class__.__name__)
        str_ += "\n\n"
        str_ += "\t" + f"geom            : {self.geom.__class__.__name__}\n"
        str_ += "\t" + f"axes            : {self.geom.axes_names}\n"
        str_ += "\t" + f"shape           : {self.geom.data_shape[::-1]}\n"
        str_ += "\t" + f"quantities      : {list(self.available_quantities)}\n"
        str_ += "\t" + f"ref. model      : {self.reference_spectral_model.tag[-1]}\n"
        str_ += "\t" + f"n_sigma         : {self.n_sigma}\n"
        str_ += "\t" + f"n_sigma_ul      : {self.n_sigma_ul}\n"
        str_ += "\t" + f"ts_threshold_ul : {self.ts_threshold_ul}\n"
        str_ += "\t" + f"sed type init   : {self.sed_type_init}\n"
        return str_.expandtabs(tabsize=2)
