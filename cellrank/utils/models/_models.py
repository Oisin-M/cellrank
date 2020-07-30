# -*- coding: utf-8 -*-
"""Models module."""

import re
from abc import ABC, abstractmethod
from copy import copy as _copy
from copy import deepcopy
from typing import Any, Tuple, Union, TypeVar, Iterable, Optional
from inspect import signature

import matplotlib as mpl
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd
import cellrank.logging as logg
from pygam import GAM as pGAM
from pygam import ExpectileGAM
from pygam.terms import s
from scipy.sparse import issparse
from sklearn.base import BaseEstimator
from cellrank.utils._docs import d
from cellrank.tools._utils import save_fig
from cellrank.utils._utils import _minmax
from scipy.ndimage.filters import convolve
from cellrank.tools._lineage import Lineage
from cellrank.tools._constants import AbsProbKey

AnnData = TypeVar("AnnData")


_dup_spaces = re.compile(r" +")


class Model(ABC):
    """
    Base class for other model classes.

    Params
    ------
    adata : :class:`anndata.AnnData`
        Annotated data object.
    model
        Underlying model.
    weight_name
        Name of the weight argument for :paramref:`model`.
    """

    def __init__(
        self,
        adata: AnnData,
        model: Any,
        filter_dropouts: Optional[Union[bool, float]] = None,
    ):
        self._adata = adata
        self._model = model
        self._filter_dropouts = filter_dropouts
        self._gene = None
        self._lineage = None

        self._x_all = None
        self._y_all = None
        self._w_all = None

        self._x = None
        self._y = None
        self._w = None

        self._x_test = None
        self._y_test = None
        self._x_hat = None
        self._y_hat = None

        self._conf_int = None

        self._dtype = np.float32

    @property
    def adata(self) -> AnnData:
        """Annotated data object."""
        return self._adata

    @property
    def model(self) -> Any:
        """The underlying model."""  # noqa
        return self._model

    @property
    def x_all(self) -> np.ndarray:
        """Original independent variables."""
        return self._x_all

    @property
    def y_all(self) -> np.ndarray:
        """Original dependent variables."""
        return self._y_all

    @property
    def w_all(self) -> np.ndarray:
        """Original weights."""
        return self._w_all

    @property
    def x(self) -> np.ndarray:
        """Independent variables used for model fitting."""
        return self._x

    @property
    def y(self) -> np.ndarray:
        """Dependent variables used for model fitting."""
        return self._y

    @property
    def w(self) -> np.ndarray:
        """Weights of independent variables used for model fitting."""
        return self._w

    @property
    def x_test(self) -> np.ndarray:
        """Independent variables used for prediction."""
        return self._x_test

    @property
    def y_test(self) -> np.ndarray:
        """Predicted values."""
        return self._y_test

    @property
    def x_hat(self) -> np.ndarray:
        """Independent variables used when calculating default confidence interval."""
        return self._x_hat

    @property
    def y_hat(self) -> np.ndarray:
        """Dependent variables used when calculating default confidence interval."""
        return self._y_hat

    @property
    def conf_int(self) -> np.ndarray:
        """Confidence interval."""
        return self._conf_int

    @d.dedent
    def prepare(
        self,
        gene: str,
        lineage: str,
        backward: bool = False,
        data_key: str = "X",
        time_key: str = "latent_time",
        start_lineage: Optional[str] = None,
        end_lineage: Optional[str] = None,
        threshold: Optional[float] = None,
        weight_threshold: Union[float, Tuple[float, float]] = (0.01, 0.01),
        filter_data: float = False,
        n_test_points: int = 200,
    ) -> "Model":
        """
        Prepare the model to be ready for fitting.

        Params
        ------
        gene
            Gene in :paramref:`adata` `.var_names`.
        lineage
            Name of a lineage in :paramref:`adata` `.uns`:paramref:`lineage_key`.
        %(backward)s
        data_key
            Key in :attr:`paramref.adata` `.layers` or `'X'` for :paramref:`adata` `.X`.
        time_key
            Key in :paramref:`adata` `.obs` where the pseudotime is stored.
        start_lineage
            Lineage from which to select cells with lowest pseudotime as starting points.
            If specified, the trends start at the earliest pseudotime within that lineage,
            otherwise they start from time `0`.
        end_lineage
            Lineage from which to select cells with highest pseudotime as endpoints.
            If specified, the trends end at the latest pseudotime within that lineage,
            otherwise, it is determined automatically.
        threshold
            Consider only cells with :paramref:`weights` > :paramref:`threshold` when estimating the testing endpoint.
            If `None`, use median of :paramref:`w`.
        weight_threshold
            Set all weights below :paramref:`weight_threshold` to either 0, or if :class:`tuple`, to the second value;/.
        filter_data
            Use only testing points for fitting.
        n_test_points
            Number of testing points. If `None`, use the original points based on :paramref:`threshold`.

        Returns
        -------
        None
            Nothing, but updates the following fields:

                - :paramref:`x`
                - :paramref:`y`
                - :paramref:`w`
                - :paramref:`x_test`
        """

        if data_key not in ["X", "obs"] + list(self.adata.layers.keys()):
            raise KeyError(
                f"Data key must be a key of `adata.layers`: `{list(self.adata.layers.keys())}`, '`obs`' or `'X'`."
            )
        if time_key not in self.adata.obs:
            raise KeyError(f"Time key `{time_key!r}` not found in `adata.obs`.")

        if data_key != "obs":
            if gene not in self.adata.var_names:
                raise KeyError(f"Gene `{gene!r}` not found in `adata.var_names`.")
        else:
            if gene not in self.adata.obs:
                raise KeyError(f"Unable to find key `{gene!r}` in `adata.obs`.")

        lineage_key = str(AbsProbKey.BACKWARD if backward else AbsProbKey.FORWARD)
        if lineage_key not in self.adata.obsm:
            raise KeyError(f"Lineage key `{lineage_key!r}` not found in `adata.obsm`.")
        if not isinstance(self.adata.obsm[lineage_key], Lineage):
            raise TypeError(
                f"Expected `adata.obsm[{lineage_key!r}]` to be of type `cellrank.tl.Lineage`, "
                f"found `{type(self.adata.obsm[lineage_key]).__name__}`."
            )

        if lineage is not None:
            _ = self.adata.obsm[lineage_key][lineage]

        if start_lineage is not None:
            if start_lineage not in self.adata.obsm[lineage_key].names:
                raise KeyError(
                    f"Start lineage `{start_lineage!r}` not found in `adata.obsm[{lineage_key!r}].names`."
                )
        if end_lineage is not None:
            if end_lineage not in self.adata.obsm[lineage_key].names:
                raise KeyError(
                    f"End lineage `{end_lineage!r}` not found in `adata.obsm[{lineage_key!r}].names`."
                )

        x = np.array(self.adata.obs[time_key]).astype(np.float64)
        gene_ix = np.where(self.adata.var_names == gene)[0]

        if data_key == "X":
            y = self.adata.X[:, gene_ix]
        elif data_key == "obs":
            y = self.adata.obs[gene].values
        elif data_key in self.adata.layers:
            y = self.adata.layers[data_key][:, gene_ix]
        else:
            raise NotImplementedError(
                f"Data key `{data_key!r}` is not yet implemented."
            )

        if issparse(y):
            y = np.asarray(y.todense())
        y = np.squeeze(y).astype(np.float64)

        if lineage is not None:
            w = (
                np.array(self.adata.obsm[lineage_key][lineage])
                .astype(self._dtype)
                .squeeze()
            )
            weight_threshold, val = (
                weight_threshold
                if isinstance(weight_threshold, (tuple, list))
                else weight_threshold,
                0,
            )
            w[w < weight_threshold] = val
        else:
            w = np.ones_like(x)

        self._x_all, self._y_all, self._w_all = x[:], y[:], w[:]

        fin_mask = np.isfinite(x)
        x, y, w = x[fin_mask], y[fin_mask], w[fin_mask]

        x, ixs = np.unique(x, return_index=True)
        y = y[ixs]
        w = w[ixs]

        ixs = np.argsort(x)
        x, y, w = x[ixs], y[ixs], w[ixs]

        if start_lineage is None or (start_lineage == lineage):
            val_start = np.min(self.adata.obs[time_key])
        else:
            from_key = "_".join(lineage_key.split("_")[1:])
            val_start = np.nanmin(
                self.adata.obs[time_key][self.adata.obs[from_key] == start_lineage]
            )

        if end_lineage is None or (end_lineage == lineage):
            if threshold is None:
                threshold = np.nanmedian(w)
            w_test = w[w > threshold]
            tmp = convolve(w_test, np.ones(10) / 10, mode="nearest")
            val_end = x[w > threshold][np.nanargmax(tmp)]
        else:
            to_key = "_".join(lineage_key.split("_")[1:])
            val_end = np.nanmax(
                self.adata.obs[time_key][self.adata.obs[to_key] == end_lineage]
            )

        if val_start > val_end:
            val_start, val_end = val_end, val_start

        x_test = (
            np.linspace(val_start, val_end, n_test_points)
            if n_test_points is not None
            else x[(x >= val_start) & (x <= val_end)]
        )

        if filter_data:
            fil = (x >= val_start) & (x <= val_end)
            x, y, w = x[fil], y[fil], w[fil]

        if self._filter_dropouts is not None:
            fil = (
                ~np.isclose(y, 0)
                if (self._filter_dropouts == 0 or self._filter_dropouts is True)
                else (y >= self._filter_dropouts)
            )
            x, y, w = x[fil], y[fil], w[fil]

        self._x, self._y, self._w = (
            self._convert(x[:]),
            self._convert(y[:]),
            self._convert(w[:]).squeeze(-1),
        )
        self._x_test = self._convert(x_test[:])

        self._gene = gene
        self._lineage = lineage

        return self

    @d.get_sectionsf("model_fit")
    @abstractmethod
    def fit(
        self,
        x: Optional[np.ndarray] = None,
        y: Optional[np.ndarray] = None,
        w: Optional[np.ndarray] = None,
        **kwargs,
    ) -> "Model":
        """
        Fit the model.

        Params
        ------
        x
            Independent variables.
        y
            Dependent variables.
        w
            Weights of :paramref:`x`.
        **kwargs
            Keyword arguments.

        Returns
        -------
        None
            Just fits the model.
        """

        self._check("_x", x)
        self._check("_y", y)
        self._check("_w", w, ndim=1)

        if self._x.shape != self._y.shape:
            raise ValueError(
                f"Inputs and targets differ in shape: `{self._x.shape}` vs. `{self._y.shape}`."
            )
        if self._y.shape[0] != self._w.shape[0]:
            raise ValueError(
                f"Inputs and weights differ in shape: `{self._y.shape[0]}` vs. `{self._w.shape[0]}`."
            )

        return self

    @d.get_sectionsf("model_predict")
    @abstractmethod
    def predict(
        self,
        x_test: Optional[np.ndarray] = None,
        key_added: Optional[str] = "_x_test",
        **kwargs,
    ) -> np.ndarray:
        """
        Run the prediction.

        Params
        ------
        x_test
            Features used for prediction.
        key_added
            Attribute name where to save the independent variables.
            If `None`, don't save them.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The predicted values.
        """

        pass

    def default_conf_int(
        self,
        x: Optional[np.ndarray] = None,
        x_test: Optional[np.ndarray] = None,
        w: Optional[np.ndarray] = None,
        **kwargs,
    ) -> np.ndarray:
        """
        Calculate a confidence interval if underlying model has no method for it.

        Params
        ------
        x
            Points used to fit the model.
        x_test
            Points for which to calculate the interval
        w
            Weights of the points used to fit the model. Used for filtering those points.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The confidence interval.
        """

        self._check("_x", x)
        self._check("_w", w, ndim=1)

        use_ixs = self.w > 0
        self._check("_x_hat", self.x[use_ixs])

        self._y_hat = self.predict(self.x_hat, key_added=None, **kwargs)
        self._y_test = self.predict(x_test, key_added="_x_test", **kwargs)

        n = np.sum(use_ixs)
        sigma = np.sqrt(((self.y_hat - self.y[use_ixs]) ** 2).sum() / (n - 2))

        stds = (
            np.sqrt(
                1
                + 1 / n
                + ((self.x_test - np.mean(self.x)) ** 2)
                / ((self.x - np.mean(self.x)) ** 2).sum()
            )
            * sigma
            / 2
        )
        stds = np.squeeze(stds)

        self._conf_int = np.c_[self._y_test - stds, self._y_test + stds]

        return self.conf_int

    @d.get_sectionsf("model_conf_int")
    @abstractmethod
    def confidence_interval(
        self, x_test: Optional[np.ndarray] = None, **kwargs
    ) -> np.ndarray:
        """
        Calculate a confidence interval.

        Use the default method if underlying model has not method for CI calculation.

        Params
        ------
        x_test
            Points for which to calculate the confidence interval.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The confidence interval.
        """

        pass

    def plot(
        self,
        figsize: Tuple[float, float] = (15, 10),
        same_plot: bool = False,
        hide_cells: bool = False,
        perc: Tuple[float, float] = None,
        abs_prob_cmap: mcolors.ListedColormap = cm.viridis,
        cell_color: str = "black",
        color: str = "black",
        alpha: float = 0.8,
        lineage_alpha: float = 0.2,
        title: Optional[str] = None,
        size: int = 15,
        lw: float = 2,
        show_cbar: bool = True,
        margins: float = 0.015,
        xlabel: str = "pseudotime",
        ylabel: str = "expression",
        show_conf_int: bool = True,
        dpi: int = None,
        fig: mpl.figure.Figure = None,
        ax: mpl.axes.Axes = None,
        return_fig: bool = False,
        save: Optional[str] = None,
    ) -> Optional[mpl.figure.Figure]:
        """
        Plot the smoothed gene expression.

        Params
        ------
        figsize
            Size of the figure.
        same_plot
            Whether to plot all trends in the same plot.
        hide_cells
            Whether to hide the cells.
        perc
            Percentile by which to clip the absorption probabilities.
        abs_prob_cmap
            Colormap to use when coloring in the absorption probabilities.
        cell_color
            Color for the cells when not coloring absorption probabilities.
        color
            Color for the lineages.
        alpha
            Alpha channel for cells.
        lineage_alpha
            Alpha channel for lineage confidence intervals.
        title
            Title of the plot.
        size
            Size of the points.
        lw
            Line width for the smoothed values.
        show_cbar
            Whether to show colorbar.
        margins
            Margins around the plot.
        xlabel
            Label on the x-axis.
        ylabel
            Label on the y-axis.
        show_conf_int
            Whether to show the confidence interval.
        dpi
            Dots per inch.
        fig
            Figure to use, if `None`, create a new one.
        ax: :class:`matplotlib.axes.Axes`
            Ax to use, if `None`, create a new one.
        return_fig
            If `True`, return the figure object.
        save
            Filename where to save the plot.
            If `None`, just shows the plots.

        Returns
        -------
        None
            Nothing, just plots the fitted model.
        """

        if fig is None or ax is None:
            fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)

        if dpi is not None:
            fig.set_dpi(dpi)

        vmin, vmax = _minmax(self.w, perc)
        if not hide_cells:
            _ = ax.scatter(
                self.x_all.squeeze(),
                self.y_all.squeeze(),
                c=cell_color
                if same_plot or np.allclose(self.w_all, 1.0)
                else self.w_all.squeeze(),
                s=size,
                cmap=abs_prob_cmap,
                vmin=vmin,
                vmax=vmax,
                alpha=alpha,
            )

        if title is None:
            title = f"{self._gene} @ {self._lineage}"

        ax.plot(self.x_test, self.y_test, color=color, lw=lw, label=title)

        ax.set_title(title)

        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)

        ax.margins(margins)

        if show_conf_int and self.conf_int is not None:
            ax.fill_between(
                self.x_test.squeeze(),
                self.conf_int[:, 0],
                self.conf_int[:, 1],
                alpha=lineage_alpha,
                color=color,
                linestyle="--",
            )

        if show_cbar and not hide_cells and not same_plot:
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
            cax, _ = mpl.colorbar.make_axes(ax, aspect=200)
            _ = mpl.colorbar.ColorbarBase(
                cax, norm=norm, cmap=abs_prob_cmap, label="Absorption probability"
            )

        if save is not None:
            save_fig(fig, save)

        if return_fig:
            return fig

    def _convert(self, value: np.ndarray) -> np.ndarray:
        was_1d = value.ndim == 1
        value = np.atleast_2d(value).astype(self._dtype)
        if was_1d:
            return np.swapaxes(value, 0, 1)
        return value

    def _check(
        self, attr_name: Optional[str], value: np.ndarray, ndim: int = 2
    ) -> None:
        if attr_name is None:
            return
        if value is None:  # already called prepare
            if not hasattr(self, attr_name):
                raise AttributeError(f"No attribute `{attr_name!r}` found.")
            if getattr(self, attr_name).ndim != ndim:
                raise ValueError(
                    f"Expected attribute `{attr_name!r}` to have `{ndim}` dimensions, "
                    f"found `{getattr(self, attr_name).ndim}` dimensions."
                )
        else:
            setattr(self, attr_name, self._convert(value))
            if attr_name.startswith("_"):
                try:
                    getattr(self, attr_name[1:])
                except AttributeError:
                    setattr(
                        self,
                        attr_name[1:],
                        property(lambda self: getattr(self, attr_name)),
                    )

    def _deepcopy_attributes(self, dst: "Model") -> None:
        for attr in [
            "_x_all",
            "_y_all",
            "_w_all",
            "_x",
            "_y",
            "_w",
            "_x_test",
            "_y_test",
            "_x_hat",
            "_y_hat",
            "_conf_int",
        ]:
            setattr(dst, attr, _copy(getattr(self, attr)))

    @abstractmethod
    def copy(self) -> "Model":
        """Return a copy of self."""
        pass

    def __copy__(self) -> "Model":
        return self.copy()

    def __deepcopy__(self, memodict={}) -> "Model":  # noqa
        res = self.copy()
        memodict[id(self)] = res
        self._deepcopy_attributes(res)
        return res

    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        return "{}[{}]".format(
            self.__class__.__name__,
            None
            if self.model is None
            else _dup_spaces.sub(" ", str(self.model).replace("\n", " ")).strip(),
        )


class SKLearnModel(Model):
    """
    Wrapper around :mod:`sklearn` model.

    Params
    ------
    adata : :class:`anndata.AnnData`
        Annotated data object.
    model
        Underlying :mod:`sklearn` model.
    filter_dropout
        Filter out all cells with expression lower than this. If `True`, the value is `0`.
    weight_name
        Name of the weight argument for :paramref:`model` `.fit`.
    """

    _fit_names = ("fit", "__init__")
    _predict_names = ("predict", "__call__")
    _weight_names = ("w", "weights", "sample_weight", "sample_weights")
    _conf_int_names = ("conf_int", "confidence_intervals")

    def __init__(
        self,
        adata: AnnData,
        model: BaseEstimator,
        filter_dropouts: Optional[Union[bool, float]] = None,
        weight_name: Optional[str] = None,
        ignore_raise: bool = False,
    ):
        super().__init__(adata, model, filter_dropouts=filter_dropouts)

        fit_name = self._find_func(self._fit_names)
        predict_name = self._find_func(self._predict_names)
        ci_name = self._find_func(self._conf_int_names, use_default=True, default=None)
        self._weight_name = None

        if weight_name is None:
            self._weight_name = self._find_weight_param(fit_name, self._weight_names)
        else:
            params = signature(getattr(self.model, fit_name)).parameters
            if not ignore_raise and weight_name not in params:
                raise ValueError(
                    f"Unable to detect `{weight_name!r}` in the signature of `{fit_name!r}`."
                    f"If it's in `kwargs`, set `ignore_raise=True.`"
                )
            self._weight_name = weight_name

        if self._weight_name is None:
            raise RuntimeError(
                f"Unable to determine weights for function `{fit_name!r}`, searched for `{self._weight_names}`. "
                f"Consider specifying it manually as `weight_name=...`."
            )

        self._fit_fn = getattr(self.model, fit_name)
        self._pred_fn = getattr(self.model, predict_name)
        self._ci_fn = None if ci_name is None else getattr(self.model, ci_name, None)

    def fit(
        self,
        x: Optional[np.ndarray] = None,
        y: Optional[np.ndarray] = None,
        w: Optional[np.ndarray] = None,
        **kwargs,
    ) -> "SKLearnModel":  # noqa
        super().fit(x, y, w, **kwargs)

        if self._weight_name is not None:
            kwargs[self._weight_name] = self._w

        self._model = self._fit_fn(self.x, self.y, **kwargs)

        return self

    def predict(
        self, x_test: Optional[np.ndarray] = None, key_added: str = "_x_test", **kwargs
    ) -> np.ndarray:
        """
        Run the prediction.

        Params
        ------
        x_test
            Features used for prediction.
        key_added
            Attribute name where to save the independent variables.
            If `None`, don't save them.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The predicted values.
        """

        self._check(key_added, x_test)

        self._y_test = self._pred_fn(self.x_test, **kwargs)
        self._y_test = np.squeeze(self._y_test)

        return self.y_test

    def confidence_interval(
        self, x_test: Optional[np.ndarray] = None, **kwargs
    ) -> np.ndarray:
        """
        Calculate a confidence interval.

        Use the default method if underlying model has not method for CI calculation.

        Params
        ------
        x_test
            Points for which to calculate the confidence interval.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The confidence interval.
        """

        if self._ci_fn is None:
            return self.default_conf_int(x_test=x_test, **kwargs)

        self._check("_x_test", x_test)
        self._conf_int = self._ci_fn(self.x_test, **kwargs)

        return self.conf_int

    def _find_func(
        self,
        func_names: Iterable[str],
        use_default: bool = False,
        default: Optional[str] = None,
    ) -> Optional[str]:
        for name in func_names:
            if hasattr(self.model, name) and callable(getattr(self.model, name)):
                return name
        if use_default:
            return default
        raise RuntimeError(
            f"Unable to find function and no default specified, tried searching `{list(func_names)}`."
        )

    def _find_weight_param(
        self, fit_name: Optional[str], param_names: Iterable[str]
    ) -> Optional[str]:
        if fit_name is None:
            return None
        for param in signature(getattr(self.model, fit_name)).parameters:
            if param in param_names:
                return param
        return None

    def copy(self) -> "SKLearnModel":
        """Return a copy of self."""
        return SKLearnModel(self.adata, deepcopy(self._model))


class GAMModel(Model):  # noqa

    _default_grid = {
        "n_splines": np.arange(6, 12),
        "lam": np.logspace(-3, 3, 5, base=2),
    }

    def __init__(
        self,
        adata: AnnData,
        n_splines: Optional[int] = 10,
        spline_order: int = 3,
        distribution: str = "normal",
        link: str = "identity",
        max_iter: int = 1000,
        expectile: Optional[float] = None,
        grid: Union[bool, dict] = False,
        filter_droupouts=None,
    ):
        term = s(
            0,
            spline_order=spline_order,
            n_splines=n_splines,
            lam=0.5,
            penalties=["derivative"],
        )
        if expectile is not None:
            if distribution != "normal" or link != "identity":
                distribution, link = "normal", "identity"
                print("expectile")
            model = ExpectileGAM(
                term, expectile=expectile, max_iter=max_iter, verbose=False
            )
        else:
            model = pGAM(
                term,
                distribution=distribution,
                link=link,
                max_iter=max_iter,
                verbose=False,
            )
        super().__init__(adata, model=model, filter_dropouts=filter_droupouts)

        if isinstance(grid, dict):
            self._grid = grid
        elif isinstance(grid, bool):
            self._grid = self._default_grid if grid else {}
        else:
            raise TypeError()

        self._use_gam_cf = distribution == "normal" and link == "identity"

    def fit(
        self,
        x: Optional[np.ndarray] = None,
        y: Optional[np.ndarray] = None,
        w: Optional[np.ndarray] = None,
        **kwargs,
    ) -> "Model":  # noqa
        super().fit(x, y, w, **kwargs)

        use_ixs = np.where(self.w > 0)[0]
        self._x = self.x[use_ixs]
        self._y = self.y[use_ixs]
        self._w = self.w[use_ixs]

        if self._grid:
            try:
                self.model.gridsearch(
                    self.x,
                    self.y,
                    weights=self.w,
                    keep_best=True,
                    progress=False,
                    **self._grid,
                )
                return self
            except Exception as e:
                logg.error(
                    f"Grid search failed, reason: `{e}`. Fitting with default values"
                )

        try:
            self.model.fit(self.x, self.y, weights=self.w)
            return self
        except Exception as e:
            raise RuntimeError(
                f"Unable to fit `{type(self).__name__}`for gene "
                f"`{self._gene!r}` in lineage `{self._lineage!r}`."
            ) from e

    def predict(
        self,
        x_test: Optional[np.ndarray] = None,
        key_added: Optional[str] = "_x_test",
        **kwargs,
    ) -> np.ndarray:  # noqa
        self._check(key_added, x_test)

        self._y_test = self.model.predict(self.x_test, **kwargs)
        self._y_test = np.squeeze(self._y_test)

        return self.y_test

    def confidence_interval(
        self, x_test: Optional[np.ndarray] = None, **kwargs
    ) -> np.ndarray:  # noqa

        self._check("_x_test", x_test)

        if self._use_gam_cf:
            self._conf_int = self.model.confidence_intervals(self.x_test, **kwargs)
        else:
            self._conf_int = self.default_conf_int(x_test=self.x_test, **kwargs)

        return self.conf_int

    def copy(self) -> "Model":  # noqa
        res = GAMModel(self.adata)

        res._use_gam_cf = self._use_gam_cf
        res._grid = deepcopy(self._grid)
        res._model = deepcopy(self.model)

        return res


class GamMGCVModel(Model):
    """
    Wrapper around R's `mgcv <https://cran.r-project.org/web/packages/mgcv/>`_ package for \
    fitting Generalized Additive Models (GAMs).

    Params
    ------
    adata : :class:`anndata.AnnData`
        Annotated data object.
    n_splines
        Number of splines for the GAMModel.
    sp
        Vector of smoothing parameters.
    family
        Family in `rpy2.robjects.r`, such as `"gaussian"` or `"poisson"`.
    filter_dropout
        Filter out all cells with expression lower than this. If `True`, the value is `0`.
    """

    def __init__(
        self,
        adata: AnnData,
        n_splines: int = 5,
        sp: float = 2,
        family: str = "gaussian",
        filter_dropouts: Optional[Union[bool, float]] = None,
        perform_import_check: bool = True,
    ):
        super().__init__(adata, model=None, filter_dropouts=filter_dropouts)
        self._n_splines = n_splines
        self._sp = sp
        self._mgcv = None
        self._family = family

        if perform_import_check:
            try:
                import rpy2  # noqa

                try:
                    from rpy2.robjects.packages import importr

                    self._mgcv = importr("mgcv")
                except rpy2.robjects.packages.PackageNotInstalledError as e:
                    raise RuntimeError(
                        "Install R library `mgcv` first as `install.packages('mgcv').`"
                    ) from e
            except ImportError:
                raise ImportError(
                    "Unable to import `rpy2`, install it first as `pip install rpy2`."
                )

    def fit(
        self,
        x: Optional[np.ndarray] = None,
        y: Optional[np.ndarray] = None,
        w: Optional[np.ndarray] = None,
        **kwargs,
    ) -> "GamMGCVModel":
        """
        Fit the model.

        Params
        ------
        x
            Independent variables.
        y
            Dependent variables.
        w
            Weights of :paramref:`x`.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`cellrank.ul.models.GamMGCVModel`
            Return fitted self.
        """

        from rpy2 import robjects
        from rpy2.robjects import pandas2ri, Formula

        super().fit(x, y, w, **kwargs)

        use_ixs = self.w > 0
        self._x = self.x[use_ixs]
        self._y = self.y[use_ixs]
        self._w = self.w[use_ixs]

        family = getattr(robjects.r, self._family, None)
        if family is None:
            family = robjects.r.gaussian

        pandas2ri.activate()
        df = pandas2ri.py2rpy(pd.DataFrame(np.c_[self.x, self.y], columns=["x", "y"]))
        self._model = self._mgcv.gam(
            Formula(f'y ~ s(x, k={self._n_splines}, bs="cr")'),
            data=df,
            sp=self._sp,
            family=family,
            weights=pd.Series(self.w),
        )
        pandas2ri.deactivate()

        return self

    def predict(
        self, x_test: Optional[np.ndarray] = None, key_added: str = "_x_test", **kwargs
    ) -> np.ndarray:
        """
        Run the prediction.

        Params
        ------
        x_test
            Features used for prediction.
        key_added
            Attribute name where to save the independent variables.
            If `None`, don't save them.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The predicted values.
        """

        from rpy2 import robjects
        from rpy2.robjects import pandas2ri

        if self.model is None:
            raise RuntimeError(
                "Trying to call an uninitialized model. To initialize it, run `.fit()` first."
            )
        if self._mgcv is None:
            raise RuntimeError(
                "Unable to fit the model, R package `mgcv` is not imported."
            )

        self._check(key_added, x_test)

        pandas2ri.activate()
        self._y_test = (
            np.array(
                robjects.r.predict(
                    self.model,
                    newdata=pandas2ri.py2rpy(pd.DataFrame(self.x_test, columns=["x"])),
                )
            )
            .squeeze()
            .astype(self._dtype)
        )
        pandas2ri.deactivate()

        return self.y_test

    def confidence_interval(
        self, x_test: Optional[np.ndarray] = None, **kwargs
    ) -> np.ndarray:
        """
        Calculate a confidence interval using the default method.

        Params
        ------
        x_test
            Points for which to calculate the confidence interval.
        **kwargs
            Keyword arguments.

        Returns
        -------
        :class:`numpy.ndarray`
            The confidence interval.
        """
        return self.default_conf_int(x_test=x_test, **kwargs)

    def copy(self) -> "GamMGCVModel":
        """Return a copy of self."""
        res = GamMGCVModel(
            self.adata,
            self._n_splines,
            self._sp,
            family=self._family,
            filter_dropouts=self._filter_dropouts,
            perform_import_check=False,
        )
        res._mgcv = self._mgcv
        return res
