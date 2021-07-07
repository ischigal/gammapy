# Licensed under a 3-clause BSD style license - see LICENSE.rst
import logging
import numpy as np
from astropy import units as u
from astropy.io.registry import IORegistryError
from astropy.table import Table, vstack
from gammapy.datasets import Datasets
from gammapy.modeling.models import PowerLawSpectralModel, TemplateSpectralModel
from gammapy.modeling import Fit
from gammapy.maps import MapAxis, RegionNDMap
from gammapy.utils.interpolation import interpolate_profile
from gammapy.utils.scripts import make_path
from gammapy.utils.pbar import progress_bar
from gammapy.utils.table import table_from_row_data, table_standardise_units_copy
from .core import (
    Estimator,
    FluxEstimate,
    DEFAULT_UNIT,
    OPTIONAL_QUANTITIES_COMMON,
    OPTIONAL_QUANTITIES,
    REQUIRED_COLUMNS,
    VALID_QUANTITIES
)
from. flux import FluxEstimator


__all__ = ["FluxPoints", "FluxPointsEstimator"]

log = logging.getLogger(__name__)


class FluxPoints(FluxEstimate):
    """Flux points container.

    The supported formats are described here: :ref:`gadf:flux-points`

    In summary, the following formats and minimum required columns are:

    * Format ``dnde``: columns ``e_ref`` and ``dnde``
    * Format ``e2dnde``: columns ``e_ref``, ``e2dnde``
    * Format ``flux``: columns ``e_min``, ``e_max``, ``flux``
    * Format ``eflux``: columns ``e_min``, ``e_max``, ``eflux``

    Parameters
    ----------
    table : `~astropy.table.Table`
        Table with flux point data

    Attributes
    ----------
    table : `~astropy.table.Table`
        Table with flux point data

    Examples
    --------
    The `FluxPoints` object is most easily created by reading a file with
    flux points given in one of the formats documented above::

        from gammapy.estimators import FluxPoints
        filename = '$GAMMAPY_DATA/hawc_crab/HAWC19_flux_points.fits'
        flux_points = FluxPoints.read(filename)
        flux_points.plot()

    An instance of `FluxPoints` can also be created by passing an instance of
    `astropy.table.Table`, which contains the required columns, such as `'e_ref'`
    and `'dnde'`. The corresponding `sed_type` has to be defined in the meta data
    of the table::

        import numpy as np
        from astropy import units as u
        from astropy.table import Table
        from gammapy.estimators import FluxPoints
        from gammapy.modeling.models import PowerLawSpectralModel

        table = Table()
        pwl = PowerLawSpectralModel()
        e_ref = np.geomspace(1, 100, 7) * u.TeV

        table["e_ref"] = e_ref
        table["dnde"] = pwl(e_ref)
        table.meta["SED_TYPE"] = "dnde"

        flux_points = FluxPoints.from_table(table)
        flux_points.plot(sed_type="flux")

    If you have flux points in a different data format, the format can be changed
    by renaming the table columns and adding meta data::


        from astropy import units as u
        from astropy.table import Table
        from gammapy.estimators import FluxPoints
        from gammapy.utils.scripts import make_path

        table = Table.read(make_path('$GAMMAPY_DATA/tests/spectrum/flux_points/flux_points_ctb_37b.txt'),
                           format='ascii.csv', delimiter=' ', comment='#')
        table.meta['SED_TYPE'] = 'dnde'
        table.rename_column('Differential_Flux', 'dnde')
        table['dnde'].unit = 'cm-2 s-1 TeV-1'

        table.rename_column('lower_error', 'dnde_errn')
        table['dnde_errn'].unit = 'cm-2 s-1 TeV-1'

        table.rename_column('upper_error', 'dnde_errp')
        table['dnde_errp'].unit = 'cm-2 s-1 TeV-1'

        table.rename_column('E', 'e_ref')
        table['e_ref'].unit = 'TeV'

        flux_points = FluxPoints.from_table(table)
        flux_points.plot(sed_type="eflux")

    Note: In order to reproduce the example you need the tests datasets folder.
    You may download it with the command
    ``gammapy download datasets --tests --out $GAMMAPY_DATA``
    """

    @classmethod
    def read(cls, filename, sed_type=None, reference_model=None, **kwargs):
        """Read flux points.

        Parameters
        ----------
        filename : str
            Filename
        sed_type : {"dnde", "flux", "eflux", "e2dnde", "likelihood"}
            Sed type
        reference_model : `SpectralModel`
            Reference spectral model
        **kwargs : dict
            Keyword arguments passed to `astropy.table.Table.read`.

        Returns
        -------
        flux_points : `FluxPoints`
            Flux points
        """
        filename = make_path(filename)

        try:
            table = Table.read(filename, **kwargs)
        except IORegistryError:
            kwargs.setdefault("format", "ascii.ecsv")
            table = Table.read(filename, **kwargs)

        return cls.from_table(table=table, sed_type=sed_type, reference_model=reference_model)

    def write(self, filename, sed_type="likelihood", **kwargs):
        """Write flux points.

        Parameters
        ----------
        filename : str
            Filename
        sed_type : {"dnde", "flux", "eflux", "e2dnde", "likelihood"}
            Sed type
        kwargs : dict
            Keyword arguments passed to `astropy.table.Table.write`.
        """
        filename = make_path(filename)
        table = self.to_table(sed_type=sed_type)
        table.write(filename, **kwargs)

    @classmethod
    def from_stack(cls, flux_points):
        """Create flux points by stacking list of flux points.

        The first `FluxPoints` object in the list is taken as a reference to infer
        column names and units for the stacked object.

        Parameters
        ----------
        flux_points : list of `FluxPoints`
            List of flux points to stack.

        Returns
        -------
        flux_points : `FluxPoints`
            Flux points without upper limit points.
        """
        reference = flux_points[0].to_table(sed_type="dnde")

        tables = []

        for fp in flux_points:
            table = fp.to_table(sed_type="dnde")
            for colname in reference.colnames:
                column = reference[colname]
                if column.unit:
                    table[colname] = table[colname].quantity.to(column.unit)
            tables.append(table[reference.colnames])

        table_stacked = vstack(tables)
        table_stacked.meta["SED_TYPE"] = "dnde"
        table_stacked.sort("e_ref")
        return cls.from_table(table=table_stacked, sed_type="dnde")

    @staticmethod
    def _convert_loglike_columns(table):
        # TODO: check sign and factor 2 here
        # https://github.com/gammapy/gammapy/pull/2546#issuecomment-554274318
        # The idea below is to support the format here:
        # https://gamma-astro-data-formats.readthedocs.io/en/latest/spectra/flux_points/index.html#likelihood-columns
        # but internally to go to the uniform "stat"

        if "loglike" in table.colnames and "stat" not in table.colnames:
            table["stat"] = 2 * table["loglike"]

        if "loglike_null" in table.colnames and "stat_null" not in table.colnames:
            table["stat_null"] = 2 * table["loglike_null"]

        if "dloglike_scan" in table.colnames and "stat_scan" not in table.colnames:
            table["stat_scan"] = 2 * table["dloglike_scan"]

        return table

    @staticmethod
    def _convert_flux_columns(table, reference_model, sed_type):
        energy_axis = MapAxis.from_table(table, format="gadf-sed-energy")

        with np.errstate(invalid="ignore", divide="ignore"):
            fluxes = reference_model.reference_fluxes(energy_axis=energy_axis)

        # TODO: handle reshaping in MapAxis
        col_ref = table[sed_type]
        factor = fluxes[f"ref_{sed_type}"].to(col_ref.unit)

        data = Table(fluxes, meta=table.meta)
        data["norm"] = col_ref / factor

        for key in OPTIONAL_QUANTITIES[sed_type]:
            if key in table.colnames:
                norm_type = key.replace(sed_type, "norm")
                data[norm_type] = table[key] / factor

        return data

    @classmethod
    def from_table(cls, table, sed_type=None, reference_model=None):
        """Create flux points from table

        Parameters
        ----------
        table : `~astropy.table.Table`
            Table
        sed_type : {"dnde", "flux", "eflux", "e2dnde", "likelihood"}
            Sed type
        reference_model : `SpectralModel`
            Reference spectral model

        Returns
        -------
        flux_points : `FluxPoints`
            Flux points
        """
        table = table_standardise_units_copy(table)

        if sed_type is None:
            sed_type = table.meta.get("SED_TYPE", None)

        if sed_type is None:
            sed_type = cls._guess_sed_type(table)

        if sed_type is None:
            raise ValueError("Specifying the sed type is required")

        cls._validate_data(data=table, sed_type=sed_type)

        if sed_type in ["dnde", "eflux", "e2dnde", "flux"]:
            if reference_model is None:
                log.warning(
                    "No reference model set for FluxPoints. Assuming point source with E^-2 spectrum."
                )

                reference_model = PowerLawSpectralModel()

            table = cls._convert_flux_columns(
                table=table, reference_model=reference_model, sed_type=sed_type
            )

        elif sed_type == "likelihood":
            table = cls._convert_loglike_columns(table)
            if reference_model is None:
                reference_model = TemplateSpectralModel(
                    energy=table["e_ref"].quantity,
                    values=table["ref_dnde"].quantity
                )
        else:
            raise ValueError(f"Not a valid SED type {sed_type}")

        maps = {}

        # We add the remaining maps
        for key in VALID_QUANTITIES:
            if key in table.colnames:
                maps[key] = RegionNDMap.from_table(table=table, colname=key, format="gadf-sed")

        return cls(data=maps, reference_spectral_model=reference_model, meta=table.meta)

    @staticmethod
    def _format_table(table):
        """Format table"""
        for column in table.colnames:
            if column.startswith(("dnde", "eflux", "flux", "e2dnde", "ref")):
                table[column].format = ".3e"
            elif column.startswith(
                ("e_min", "e_max", "e_ref", "sqrt_ts", "norm", "ts", "stat")
            ):
                table[column].format = ".3f"

        return table

    def to_table(self, sed_type="likelihood", format="gadf-sed", formatted=False):
        """Create table for a given SED type.

        Parameters
        ----------
        sed_type : {"likelihood", "dnde", "e2dnde", "flux", "eflux"}
            sed type to convert to. Default is `likelihood`
        format : {"gadf-sed"}
            Format
        formatted : bool
            Formatted version with column formats applied. Numerical columns are
            formatted to .3f and .3e respectively.

        Returns
        -------
        table : `~astropy.table.Table`
            Flux points table
        """
        table = Table()

        all_quantities = (
            REQUIRED_COLUMNS[sed_type] +
            OPTIONAL_QUANTITIES[sed_type] +
            OPTIONAL_QUANTITIES_COMMON
        )

        idx = (Ellipsis, 0, 0)

        # TODO: simplify...
        for quantity in all_quantities:
            if quantity == "e_ref":
                table["e_ref"] = self.energy_ref
            elif quantity == "e_min":
                table["e_min"] = self.energy_min
            elif quantity == "e_max":
                table["e_max"] = self.energy_max
            elif quantity == "ref_dnde":
                table["ref_dnde"] = self.dnde_ref[idx]
            elif quantity == "ref_flux":
                table["ref_flux"] = self.flux_ref[idx]
            elif quantity == "ref_eflux":
                table["ref_eflux"] = self.eflux_ref[idx]
            else:
                data = getattr(self, quantity, None)
                if data:
                    table[quantity] = data.quantity[idx]

        if sed_type == "likelihood":
            try:
                norm_axis = self.stat_scan.geom.axes["norm"]
                table["norm_scan"] = norm_axis.center.reshape((1, -1))
                table["stat"] = self.stat.data[idx]
                table["stat_scan"] = self.stat_scan.data[idx]
            except AttributeError:
                pass

        table.meta["SED_TYPE"] = sed_type

        if formatted:
            table = self._format_table(table=table)

        return table

    @staticmethod
    def _energy_ref_lafferty(model, energy_min, energy_max):
        """Helper for `to_sed_type`.

        Compute energy_ref that the value at energy_ref corresponds
        to the mean value between energy_min and energy_max.
        """
        flux = model.integral(energy_min, energy_max)
        dnde_mean = flux / (energy_max - energy_min)
        return model.inverse(dnde_mean)

    @staticmethod
    def _guess_sed_type(table):
        """Guess SED type from table content."""
        valid_sed_types = list(REQUIRED_COLUMNS.keys())
        for sed_type in valid_sed_types:
            required = set(REQUIRED_COLUMNS[sed_type])
            if required.issubset(table.colnames):
                return sed_type

    @staticmethod
    def _guess_sed_type_from_unit(unit):
        """Guess SED type from unit."""
        for sed_type, default_unit in DEFAULT_UNIT.items():
            if unit.is_equivalent(default_unit):
                return sed_type

    @staticmethod
    def _get_y_energy_unit(y_unit):
        """Get energy part of the given y unit."""
        try:
            return [_ for _ in y_unit.bases if _.physical_type == "energy"][0]
        except IndexError:
            return u.Unit("TeV")

    def _plot_get_energy_err(self):
        """Compute energy error for given sed type"""
        try:
            energy_min = self.energy_min
            energy_max = self.energy_max
            energy_ref = self.energy_ref
            x_err = ((energy_ref - energy_min), (energy_max - energy_ref))
        except KeyError:
            x_err = None
        return x_err

    def _plot_get_flux_err(self, sed_type=None):
        """Compute flux error for given sed type"""
        try:
            # asymmetric error
            y_errn = getattr(self, sed_type + "_errn").quantity.squeeze()
            y_errp = getattr(self, sed_type + "_errp").quantity.squeeze()
            y_err = (y_errn, y_errp)
        except AttributeError:
            try:
                # symmetric error
                y_err = getattr(self, sed_type + "_err").quantity.squeeze()
                y_err = (y_err, y_err)
            except AttributeError:
                # no error at all
                y_err = None
        return y_err

    def plot(
        self, ax=None, energy_unit="TeV", flux_unit=None, energy_power=0, sed_type="dnde", **kwargs
    ):
        """Plot flux points.

        Parameters
        ----------
        ax : `~matplotlib.axes.Axes`
            Axis object to plot on.
        energy_unit : str, `~astropy.units.Unit`, optional
            Unit of the energy axis
        flux_unit : str, `~astropy.units.Unit`, optional
            Unit of the flux axis
        energy_power : int
            Power of energy to multiply y axis with
        sed_type : {"dnde", "flux", "eflux", "e2dnde"}
            Sed type
        kwargs : dict
            Keyword arguments passed to :func:`matplotlib.pyplot.errorbar`

        Returns
        -------
        ax : `~matplotlib.axes.Axes`
            Axis object
        """
        if not self.norm.geom.is_region:
            raise ValueError("Plotting only supported for flux points")

        import matplotlib.pyplot as plt

        if ax is None:
            ax = plt.gca()

        y_unit = u.Unit(flux_unit or DEFAULT_UNIT[sed_type])

        y = getattr(self, sed_type).quantity.squeeze().to(y_unit)
        x = self.energy_ref.to(energy_unit)

        # get errors and ul
        is_ul = self.is_ul.data.squeeze()
        x_err_all = self._plot_get_energy_err()
        y_err_all = self._plot_get_flux_err(sed_type=sed_type)

        # handle energy power
        energy_unit_y = self._get_y_energy_unit(y_unit)
        y_unit = y.unit * energy_unit_y ** energy_power
        y = (y * np.power(x, energy_power)).to(y_unit)

        y_err, x_err = None, None

        if y_err_all:
            y_errn = (y_err_all[0] * np.power(x, energy_power)).to(y_unit)
            y_errp = (y_err_all[1] * np.power(x, energy_power)).to(y_unit)
            y_err = (y_errn[~is_ul].to_value(y_unit), y_errp[~is_ul].to_value(y_unit))

        if x_err_all:
            x_errn, x_errp = x_err_all
            x_err = (
                x_errn[~is_ul].to_value(energy_unit),
                x_errp[~is_ul].to_value(energy_unit),
            )

        # set flux points plotting defaults
        kwargs.setdefault("marker", "+")
        kwargs.setdefault("ls", "None")

        ebar = ax.errorbar(
            x[~is_ul].value, y[~is_ul].value, yerr=y_err, xerr=x_err, **kwargs
        )

        if is_ul.any():
            if x_err_all:
                x_errn, x_errp = x_err_all
                x_err = (
                    x_errn[is_ul].to_value(energy_unit),
                    x_errp[is_ul].to_value(energy_unit),
                )

            y_ul = getattr(self, sed_type + "_ul").quantity.squeeze()
            y_ul = (y_ul * np.power(x, energy_power)).to(y_unit)

            y_err = (0.5 * y_ul[is_ul].value, np.zeros_like(y_ul[is_ul].value))

            kwargs.setdefault("color", ebar[0].get_color())

            # pop label keyword to avoid that it appears twice in the legend
            kwargs.pop("label", None)
            ax.errorbar(
                x[is_ul].value,
                y_ul[is_ul].value,
                xerr=x_err,
                yerr=y_err,
                uplims=True,
                **kwargs,
            )

        ax.set_xscale("log", nonpositive="clip")
        ax.set_yscale("log", nonpositive="clip")
        ax.set_xlabel(f"Energy ({energy_unit})")
        ax.set_ylabel(f"{sed_type} ({y_unit})")
        return ax

    def plot_ts_profiles(
        self,
        ax=None,
        energy_unit="TeV",
        add_cbar=True,
        y_values=None,
        y_unit=None,
        sed_type="dnde",
        **kwargs,
    ):
        """Plot fit statistic SED profiles as a density plot.
        Parameters
        ----------
        ax : `~matplotlib.axes.Axes`
            Axis object to plot on.
        energy_unit : str, `~astropy.units.Unit`, optional
            Unit of the energy axis
        add_cbar : bool
            Whether to add a colorbar to the plot.
        y_values : `astropy.units.Quantity`
            Array of y-values to use for the fit statistic profile evaluation.
        y_unit : str or `astropy.units.Unit`
            Unit to use for the y-axis.
        sed_type : {"dnde", "flux", "eflux", "e2dnde"}
            Sed type
        kwargs : dict
            Keyword arguments passed to :func:`matplotlib.pyplot.pcolormesh`

        Returns
        -------
        ax : `~matplotlib.axes.Axes`
            Axis object
        """
        import matplotlib.pyplot as plt

        if ax is None:
            ax = plt.gca()

        y_unit = u.Unit(y_unit or DEFAULT_UNIT[sed_type])

        if y_values is None:
            ref_values = getattr(self, sed_type + "_ref").to_value(y_unit)
            y_values = np.geomspace(
                0.2 * ref_values.min(), 5 * ref_values.max(), 500
            )
            y_values = u.Quantity(y_values, y_unit, copy=False)

        x = self.energy_axis.edges.to(energy_unit)

        z = np.empty((len(self.norm.data), len(y_values)))

        stat_scan = self.stat_scan
        norm_scan = stat_scan.geom.axes["norm"].center.to_value("")

        for idx in range(self.energy_axis.nbin):
            y_ref = getattr(self, sed_type + "_ref")[idx, 0, 0]
            norm = (y_values / y_ref).to_value("")
            ts_scan = stat_scan.data[idx, :, 0, 0] - self.stat.data[idx, 0, 0]
            interp = interpolate_profile(norm_scan, ts_scan)
            z[idx] = interp((norm,))

        kwargs.setdefault("vmax", 0)
        kwargs.setdefault("vmin", -4)
        kwargs.setdefault("zorder", 0)
        kwargs.setdefault("cmap", "Blues")
        kwargs.setdefault("linewidths", 0)
        kwargs.setdefault("shading", "auto")

        # clipped values are set to NaN so that they appear white on the plot
        z[-z < kwargs["vmin"]] = np.nan
        caxes = ax.pcolormesh(x.value, y_values.to_value(y_unit), -z.T, **kwargs)

        ax.set_xscale("log", nonpositive="clip")
        ax.set_yscale("log", nonpositive="clip")
        ax.set_xlabel(f"Energy ({energy_unit})")
        ax.set_ylabel(f"{sed_type} ({y_values.unit})")

        if add_cbar:
            label = "Fit statistic difference"
            ax.figure.colorbar(caxes, ax=ax, label=label)

        return ax


class FluxPointsEstimator(FluxEstimator):
    """Flux points estimator.

    Estimates flux points for a given list of datasets, energies and spectral model.

    To estimate the flux point the amplitude of the reference spectral model is
    fitted within the energy range defined by the energy group. This is done for
    each group independently. The amplitude is re-normalized using the "norm" parameter,
    which specifies the deviation of the flux from the reference model in this
    energy group. See https://gamma-astro-data-formats.readthedocs.io/en/latest/spectra/binned_likelihoods/index.html
    for details.

    The method is also described in the Fermi-LAT catalog paper
    https://ui.adsabs.harvard.edu/#abs/2015ApJS..218...23A
    or the HESS Galactic Plane Survey paper
    https://ui.adsabs.harvard.edu/#abs/2018A%26A...612A...1H

    Parameters
    ----------
    energy_edges : `~astropy.units.Quantity`
        Energy edges of the flux point bins.
    source : str or int
        For which source in the model to compute the flux points.
    norm_min : float
        Minimum value for the norm used for the fit statistic profile evaluation.
    norm_max : float
        Maximum value for the norm used for the fit statistic profile evaluation.
    norm_n_values : int
        Number of norm values used for the fit statistic profile.
    norm_values : `numpy.ndarray`
        Array of norm values to be used for the fit statistic profile.
    n_sigma : int
        Number of sigma to use for asymmetric error computation. Default is 1.
    n_sigma_ul : int
        Number of sigma to use for upper limit computation. Default is 2.
    selection_optional : list of str
        Which additional quantities to estimate. Available options are:

            * "all": all the optional steps are executed
            * "errn-errp": estimate asymmetric errors on flux.
            * "ul": estimate upper limits.
            * "scan": estimate fit statistic profiles.

        Default is None so the optionnal steps are not executed.
    fit : `Fit`
        Fit instance specifying the backend and fit options.
    reoptimize : bool
        Re-optimize other free model parameters. Default is True.
    """

    tag = "FluxPointsEstimator"
    _available_selection_optional = ["errn-errp", "ul", "scan"]

    def __init__(
        self,
        energy_edges=[1, 10] * u.TeV,
        **kwargs
    ):
        self.energy_edges = energy_edges

        fit = Fit(confidence_opts={"backend": "scipy"})
        kwargs.setdefault("fit", fit)
        super().__init__(**kwargs)

    def run(self, datasets):
        """Run the flux point estimator for all energy groups.

        Parameters
        ----------
        datasets : list of `~gammapy.datasets.Dataset`
            Datasets
        Returns
        -------
        flux_points : `FluxPoints`
            Estimated flux points.
        """
        datasets = Datasets(datasets).copy()

        rows = []

        for energy_min, energy_max in progress_bar(
            zip(self.energy_edges[:-1], self.energy_edges[1:]),
            desc="Energy bins"
        ):
            row = self.estimate_flux_point(
                datasets, energy_min=energy_min, energy_max=energy_max,
            )
            rows.append(row)

        table = table_from_row_data(rows=rows, meta={"SED_TYPE": "likelihood"})
        model = datasets.models[self.source]
        return FluxPoints.from_table(table, reference_model=model.spectral_model.copy())

    def estimate_flux_point(self, datasets, energy_min, energy_max):
        """Estimate flux point for a single energy group.

        Parameters
        ----------
        datasets : Datasets
            Datasets
        energy_min, energy_max : `~astropy.units.Quantity`
            Energy bounds to compute the flux point for.

        Returns
        -------
        result : dict
            Dict with results for the flux point.
        """
        datasets_sliced = datasets.slice_by_energy(
            energy_min=energy_min, energy_max=energy_max
        )

        datasets_sliced.models = datasets.models.copy()

        result = self.estimate_counts(
            datasets, energy_min=energy_min, energy_max=energy_max
        )

        result.update(super().run(datasets=datasets_sliced))

        return result

    @staticmethod
    def estimate_counts(datasets, energy_min, energy_max):
        """Estimate counts for the flux point.

        Parameters
        ----------
        datasets : Datasets
            Datasets
        energy_min, energy_max : `~astropy.units.Quantity`
            Energy bounds to compute the flux point for.

        Returns
        -------
        result : dict
            Dict with an array with one entry per dataset with counts for the flux point.
        """
        counts = []

        for dataset in datasets:
            mask = dataset.counts.geom.energy_mask(
                energy_min=energy_min, energy_max=energy_max, round_to_edge=True
            )
            if dataset.mask is not None:
                mask = mask & dataset.mask

            counts.append(dataset.counts.data[mask].sum())

        return {"counts": np.array(counts, dtype=int)}
