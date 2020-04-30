# -*- coding: utf-8 -*-
from cellrank.tools._constants import _lin_names, _colors, LinKey
from cellrank.tools._utils import _create_categorical_colors
from cellrank.tools._markov_chain import MarkovChain
from cellrank.tools._lineage import Lineage

import cellrank as cr
import scanpy as sc
import numpy as np

from pathlib import Path
from anndata import AnnData
from typing import Callable, Tuple


def test_fwd(func: Callable) -> Callable:
    def decorator(adata_mc_fwd: Tuple[AnnData, MarkovChain], tmpdir):
        adata, _ = adata_mc_fwd
        adata = adata.copy()

        dirname = func.__name__
        tmpdir.mkdir(dirname)

        return func(
            adata,
            Path(tmpdir.dirname) / dirname / "tmp.h5ad",
            str(LinKey.FORWARD),
            adata.obsm[str(LinKey.FORWARD)].shape[1],
        )

    return decorator


class TestRead:
    @test_fwd
    def test_no_lineage(self, adata: AnnData, path: Path, lin_key: str, _: int):
        del adata.obsm[lin_key]

        sc.write(path, adata)
        adata_new = cr.read(path)

        assert adata_new is not adata  # sanity check
        assert lin_key not in adata_new.obsm.keys()

    @test_fwd
    def test_no_names(self, adata: AnnData, path: Path, lin_key: str, n_lins: int):
        names_key = _lin_names(lin_key)
        del adata.uns[names_key]

        sc.write(path, adata)
        adata_new = cr.read(path)
        lins = adata_new.obsm[lin_key]

        assert isinstance(lins, Lineage)
        np.testing.assert_array_equal(
            lins.names, [f"Lineage {i}" for i in range(n_lins)]
        )
        np.testing.assert_array_equal(lins.names, adata_new[names_key])

    @test_fwd
    def test_no_colors(self, adata: AnnData, path: Path, lin_key: str, n_lins: int):
        colors_key = _colors(lin_key)
        del adata.uns[colors_key]

        sc.write(path, adata)
        adata_new = cr.read(path)
        lins = adata_new.obsm[lin_key]

        assert isinstance(lins, Lineage)
        np.testing.assert_array_equal(lins.colors, _create_categorical_colors(n_lins))
        np.testing.assert_array_equal(lins.colors, adata_new[colors_key])

    @test_fwd
    def test_wrong_names_length(
        self, adata: AnnData, path: Path, lin_key: str, n_lins: int
    ):
        names_key = _lin_names(lin_key)
        adata.uns[names_key] += ["foo", "bar", "baz"]

        sc.write(path, adata)
        adata_new = cr.read(path)
        lins = adata_new.obsm[lin_key]

        assert isinstance(lins, Lineage)
        np.testing.assert_array_equal(
            lins.names, [f"Lineage {i}" for i in range(n_lins)]
        )
        np.testing.assert_array_equal(lins.names, adata_new[names_key])

    @test_fwd
    def test_not_unique_names(
        self, adata: AnnData, path: Path, lin_key: str, n_lins: int
    ):
        names_key = _lin_names(lin_key)
        adata.uns[names_key] += [adata.uns[names_key][0]]

        sc.write(path, adata)
        adata_new = cr.read(path)
        lins = adata_new.obsm[lin_key]

        assert isinstance(lins, Lineage)
        np.testing.assert_array_equal(
            lins.names, [f"Lineage {i}" for i in range(n_lins)]
        )
        np.testing.assert_array_equal(lins.names, adata_new[names_key])

    @test_fwd
    def test_wrong_colors_length(
        self, adata: AnnData, path: Path, lin_key: str, n_lins: int
    ):
        colors_key = _colors(lin_key)
        adata.uns[colors_key] += [adata.uns[colors_key][0]]

        sc.write(path, adata)
        adata_new = cr.read(path)
        lins = adata_new.obsm[lin_key]

        assert isinstance(lins, Lineage)
        np.testing.assert_array_equal(lins.colors, _create_categorical_colors(n_lins))
        np.testing.assert_array_equal(lins.colors, adata_new[colors_key])

    @test_fwd
    def test_colors_not_colorlike(
        self, adata: AnnData, path: Path, lin_key: str, n_lins: int
    ):
        colors_key = _colors(lin_key)
        adata.uns[colors_key][0] = "foo"

        sc.write(path, adata)
        adata_new = cr.read(path)
        lins = adata_new.obsm[lin_key]

        assert isinstance(lins, Lineage)
        np.testing.assert_array_equal(lins.colors, _create_categorical_colors(n_lins))
        np.testing.assert_array_equal(lins.colors, adata_new[colors_key])
