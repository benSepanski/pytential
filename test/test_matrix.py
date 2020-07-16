from __future__ import division, absolute_import, print_function

__copyright__ = """
Copyright (C) 2015 Andreas Kloeckner
Copyright (C) 2018 Alexandru Fikl
"""

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from functools import partial

import numpy as np
import numpy.linalg as la

import pyopencl as cl
import pyopencl.array

from pytools.obj_array import make_obj_array, is_obj_array

from sumpy.tools import BlockIndexRanges, MatrixBlockIndexRanges
from sumpy.symbolic import USE_SYMENGINE

from pytential import bind, sym
from pytential import GeometryCollection

from meshmode.array_context import PyOpenCLArrayContext
from meshmode.mesh.generation import (  # noqa
        ellipse, NArmedStarfish, make_curve_mesh, generate_torus)

import pytest
from pyopencl.tools import (  # noqa
        pytest_generate_tests_for_pyopencl
        as pytest_generate_tests)

try:
    import matplotlib.pyplot as pt
except ImportError:
    pass


def _build_geometry(actx,
        ambient_dim=2,
        nelements=30,
        target_order=7,
        qbx_order=4,
        curve_f=None,
        auto_where=None):

    if curve_f is None:
        curve_f = NArmedStarfish(5, 0.25)

    if ambient_dim == 2:
        mesh = make_curve_mesh(curve_f,
                np.linspace(0, 1, nelements + 1),
                target_order)
    elif ambient_dim == 3:
        mesh = generate_torus(10.0, 2.0, order=target_order)
    else:
        raise ValueError("unsupported ambient dimension")

    from meshmode.discretization import Discretization
    from meshmode.discretization.poly_element import \
            InterpolatoryQuadratureSimplexGroupFactory
    from pytential.qbx import QBXLayerPotentialSource
    density_discr = Discretization(actx, mesh,
            InterpolatoryQuadratureSimplexGroupFactory(target_order))

    qbx = QBXLayerPotentialSource(density_discr,
            fine_order=4 * target_order,
            qbx_order=qbx_order,
            fmm_order=False)
    places = GeometryCollection(qbx, auto_where=auto_where)

    return places, places.auto_source


def _build_block_index(actx, discr,
                       nblks=10,
                       factor=1.0,
                       use_tree=True):
    max_particles_in_box = discr.ndofs // nblks

    # create index ranges
    from pytential.linalg.proxy import partition_by_nodes
    indices = partition_by_nodes(actx, discr,
            use_tree=use_tree,
            max_nodes_in_box=max_particles_in_box)

    if abs(factor - 1.0) < 1.0e-14:
        return indices

    # randomly pick a subset of points
    # FIXME: this needs porting in sumpy.tools.BlockIndexRanges
    indices = indices.get(actx.queue)

    indices_ = np.empty(indices.nblocks, dtype=np.object)
    for i in range(indices.nblocks):
        iidx = indices.block_indices(i)
        isize = int(factor * len(iidx))
        isize = max(1, min(isize, len(iidx)))

        indices_[i] = np.sort(
                np.random.choice(iidx, size=isize, replace=False))

    ranges_ = actx.from_numpy(np.cumsum([0] + [r.shape[0] for r in indices_]))
    indices_ = actx.from_numpy(np.hstack(indices_))

    indices = BlockIndexRanges(actx.context,
            actx.freeze(indices_), actx.freeze(ranges_))

    return indices


def _build_op(lpot_id,
              k=0,
              ambient_dim=2,
              source=sym.DEFAULT_SOURCE,
              target=sym.DEFAULT_TARGET,
              qbx_forced_limit="avg"):
    from sumpy.kernel import LaplaceKernel, HelmholtzKernel

    if k:
        knl = HelmholtzKernel(ambient_dim)
        knl_kwargs = {"k": k}
    else:
        knl = LaplaceKernel(ambient_dim)
        knl_kwargs = {}

    lpot_kwargs = {
            "qbx_forced_limit": qbx_forced_limit,
            "source": source,
            "target": target}
    lpot_kwargs.update(knl_kwargs)
    if lpot_id == 1:
        # scalar single-layer potential
        u_sym = sym.var("u")
        op = sym.S(knl, u_sym, **lpot_kwargs)
    elif lpot_id == 2:
        # scalar combination of layer potentials
        u_sym = sym.var("u")
        op = sym.S(knl, 0.3 * u_sym, **lpot_kwargs) \
             + sym.D(knl, 0.5 * u_sym, **lpot_kwargs)
    elif lpot_id == 3:
        # vector potential
        u_sym = sym.make_sym_vector("u", 2)
        u0_sym, u1_sym = u_sym

        op = make_obj_array([
            sym.Sp(knl, u0_sym, **lpot_kwargs)
            + sym.D(knl, u1_sym, **lpot_kwargs),
            sym.S(knl, 0.4 * u0_sym, **lpot_kwargs)
            + 0.3 * sym.D(knl, u0_sym, **lpot_kwargs)
            ])
    else:
        raise ValueError("Unknown lpot_id: {}".format(lpot_id))

    op = 0.5 * u_sym + op

    return op, u_sym, knl_kwargs


def _max_block_error(mat, blk, index_set):
    error = -np.inf
    for i in range(index_set.nblocks):
        mat_i = index_set.take(mat, i)
        blk_i = index_set.block_take(blk, i)

        error = max(error, la.norm(mat_i - blk_i) / la.norm(mat_i))

    return error


@pytest.mark.skipif(USE_SYMENGINE,
        reason="https://gitlab.tiker.net/inducer/sumpy/issues/25")
@pytest.mark.parametrize("k", [0, 42])
@pytest.mark.parametrize("curve_f", [
    partial(ellipse, 3),
    NArmedStarfish(5, 0.25)])
@pytest.mark.parametrize("lpot_id", [2, 3])
def test_matrix_build(ctx_factory, k, curve_f, lpot_id, visualize=False):
    cl_ctx = ctx_factory()
    queue = cl.CommandQueue(cl_ctx)
    actx = PyOpenCLArrayContext(queue)

    # prevent cache 'splosion
    from sympy.core.cache import clear_cache
    clear_cache()

    target_order = 7
    qbx_order = 4
    nelements = 30
    mesh = make_curve_mesh(curve_f,
            np.linspace(0, 1, nelements + 1),
            target_order)

    from meshmode.discretization import Discretization
    from meshmode.discretization.poly_element import \
            InterpolatoryQuadratureSimplexGroupFactory
    pre_density_discr = Discretization(actx, mesh,
            InterpolatoryQuadratureSimplexGroupFactory(target_order))

    from pytential.qbx import QBXLayerPotentialSource
    qbx = QBXLayerPotentialSource(pre_density_discr,
            4 * target_order,
            qbx_order=qbx_order,
            # Don't use FMM for now
            fmm_order=False)

    from pytential.qbx.refinement import refine_geometry_collection
    places = GeometryCollection(qbx)
    places = refine_geometry_collection(places,
            kernel_length_scale=(5 / k if k else None))

    source = places.auto_source.to_stage1()
    density_discr = places.get_discretization(source.geometry)

    op, u_sym, knl_kwargs = _build_op(lpot_id, k=k,
            source=places.auto_source,
            target=places.auto_target)
    bound_op = bind(places, op)

    from pytential.symbolic.execution import build_matrix
    mat = build_matrix(actx, places, op, u_sym).get()

    if visualize:
        from sumpy.tools import build_matrix as build_matrix_via_matvec
        mat2 = bound_op.scipy_op(actx, "u", dtype=mat.dtype, **knl_kwargs)
        mat2 = build_matrix_via_matvec(mat2)
        print(la.norm((mat - mat2).real, "fro") / la.norm(mat2.real, "fro"),
              la.norm((mat - mat2).imag, "fro") / la.norm(mat2.imag, "fro"))

        pt.subplot(121)
        pt.imshow(np.log10(np.abs(1.0e-20 + (mat - mat2).real)))
        pt.colorbar()
        pt.subplot(122)
        pt.imshow(np.log10(np.abs(1.0e-20 + (mat - mat2).imag)))
        pt.colorbar()
        pt.show()

    if visualize:
        pt.subplot(121)
        pt.imshow(mat.real)
        pt.colorbar()
        pt.subplot(122)
        pt.imshow(mat.imag)
        pt.colorbar()
        pt.show()

    from pytential.utils import unflatten_from_numpy, flatten_to_numpy
    np.random.seed(12)
    for i in range(5):
        if is_obj_array(u_sym):
            u = make_obj_array([
                np.random.randn(density_discr.ndofs)
                for _ in range(len(u_sym))
                ])
        else:
            u = np.random.randn(density_discr.ndofs)
        u_dev = unflatten_from_numpy(actx, density_discr, u)

        res_matvec = np.hstack(
                flatten_to_numpy(actx, bound_op(actx, u=u_dev))
                )
        res_mat = mat.dot(np.hstack(u))

        abs_err = la.norm(res_mat - res_matvec, np.inf)
        rel_err = abs_err / la.norm(res_matvec, np.inf)

        print("AbsErr {:.5e} RelErr {:.5e}".format(abs_err, rel_err))
        assert rel_err < 1e-13, 'iteration: {}'.format(i)


@pytest.mark.parametrize("factor", [1.0, 0.6])
@pytest.mark.parametrize("ambient_dim", [2, 3])
@pytest.mark.parametrize("lpot_id", [1, 2])
def test_p2p_block_builder(ctx_factory, factor, ambient_dim, lpot_id,
                           visualize=False):
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)
    actx = PyOpenCLArrayContext(queue)

    # prevent cache explosion
    from sympy.core.cache import clear_cache
    clear_cache()

    place_ids = (
            sym.DOFDescriptor(
                geometry=sym.DEFAULT_SOURCE,
                discr_stage=sym.QBX_SOURCE_STAGE2),
            sym.DOFDescriptor(geometry=sym.DEFAULT_TARGET)
            )
    target_order = 2 if ambient_dim == 3 else 7

    places, dofdesc = _build_geometry(actx,
            target_order=target_order,
            ambient_dim=ambient_dim,
            auto_where=place_ids)
    op, u_sym, _ = _build_op(lpot_id,
            ambient_dim=ambient_dim,
            source=places.auto_source,
            target=places.auto_target)

    dd = places.auto_source
    density_discr = places.get_discretization(dd.geometry, dd.discr_stage)
    index_set = _build_block_index(actx, density_discr, factor=factor)
    index_set = MatrixBlockIndexRanges(ctx, index_set, index_set)

    from pytential.symbolic.execution import _prepare_expr
    expr = _prepare_expr(places, op)

    from pytential.symbolic.matrix import P2PMatrixBuilder
    mbuilder = P2PMatrixBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            context={},
            exclude_self=True)
    mat = mbuilder(expr)

    from pytential.symbolic.matrix import FarFieldBlockBuilder
    mbuilder = FarFieldBlockBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            index_set=index_set,
            context={},
            exclude_self=True)
    blk = mbuilder(expr)

    index_set = index_set.get(actx.queue)
    if visualize and ambient_dim == 2:
        blk_full = np.zeros_like(mat)
        mat_full = np.zeros_like(mat)

        for i in range(index_set.nblocks):
            itgt, isrc = index_set.block_indices(i)

            blk_full[np.ix_(itgt, isrc)] = index_set.block_take(blk, i)
            mat_full[np.ix_(itgt, isrc)] = index_set.take(mat, i)

        _, (ax1, ax2) = pt.subplots(1, 2,
                figsize=(10, 8), dpi=300, constrained_layout=True)
        ax1.imshow(blk_full)
        ax1.set_title('FarFieldBlockBuilder')
        ax2.imshow(mat_full)
        ax2.set_title('P2PMatrixBuilder')
        pt.savefig("test_p2p_block_{}d_{:.1f}.png".format(ambient_dim, factor))

    assert _max_block_error(mat, blk, index_set) < 1.0e-14


@pytest.mark.parametrize("factor", [1.0, 0.6])
@pytest.mark.parametrize("ambient_dim", [2, 3])
@pytest.mark.parametrize("lpot_id", [1, 2])
def test_qbx_block_builder(ctx_factory, factor, ambient_dim, lpot_id,
                           visualize=False):
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)
    actx = PyOpenCLArrayContext(queue)

    # prevent cache explosion
    from sympy.core.cache import clear_cache
    clear_cache()

    place_ids = (
            sym.DOFDescriptor(
                geometry=sym.DEFAULT_SOURCE,
                discr_stage=sym.QBX_SOURCE_STAGE2),
            sym.DOFDescriptor(geometry=sym.DEFAULT_TARGET)
            )
    target_order = 2 if ambient_dim == 3 else 7

    places, _ = _build_geometry(actx,
            target_order=target_order,
            ambient_dim=ambient_dim,
            auto_where=place_ids)
    op, u_sym, _ = _build_op(lpot_id,
            ambient_dim=ambient_dim,
            source=places.auto_source,
            target=places.auto_target,
            qbx_forced_limit="avg")

    from pytential.symbolic.execution import _prepare_expr
    expr = _prepare_expr(places, op)

    dd = places.auto_source
    density_discr = places.get_discretization(dd.geometry, dd.discr_stage)
    index_set = _build_block_index(actx, density_discr, factor=factor)
    index_set = MatrixBlockIndexRanges(ctx, index_set, index_set)

    from pytential.symbolic.matrix import NearFieldBlockBuilder
    mbuilder = NearFieldBlockBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            index_set=index_set,
            context={})
    blk = mbuilder(expr)

    from pytential.symbolic.matrix import MatrixBuilder
    mbuilder = MatrixBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            context={})
    mat = mbuilder(expr)

    index_set = index_set.get(queue)
    if visualize:
        blk_full = np.zeros_like(mat)
        mat_full = np.zeros_like(mat)

        for i in range(index_set.nblocks):
            itgt, isrc = index_set.block_indices(i)

            blk_full[np.ix_(itgt, isrc)] = index_set.block_take(blk, i)
            mat_full[np.ix_(itgt, isrc)] = index_set.take(mat, i)

        _, (ax1, ax2) = pt.subplots(1, 2,
                figsize=(10, 8), constrained_layout=True)
        ax1.imshow(mat_full)
        ax1.set_title('MatrixBuilder')
        ax2.imshow(blk_full)
        ax2.set_title('NearFieldBlockBuilder')
        pt.savefig("test_qbx_block_builder.png", dpi=300)

    assert _max_block_error(mat, blk, index_set) < 1.0e-14


@pytest.mark.parametrize(('source_discr_stage', 'target_discr_stage'),
        [(sym.QBX_SOURCE_STAGE1, sym.QBX_SOURCE_STAGE1),
         (sym.QBX_SOURCE_STAGE2, sym.QBX_SOURCE_STAGE2)])
def test_build_matrix_places(ctx_factory,
        source_discr_stage, target_discr_stage, visualize=False):
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)
    actx = PyOpenCLArrayContext(queue)

    # prevent cache explosion
    from sympy.core.cache import clear_cache
    clear_cache()

    qbx_forced_limit = -1
    place_ids = (
            sym.DOFDescriptor(
                geometry=sym.DEFAULT_SOURCE,
                discr_stage=source_discr_stage),
            sym.DOFDescriptor(geometry=sym.DEFAULT_TARGET)
            )

    # build test operators
    places, _ = _build_geometry(actx,
            nelements=8,
            target_order=2,
            ambient_dim=2,
            curve_f=partial(ellipse, 1.0),
            auto_where=place_ids)
    op, u_sym, _ = _build_op(lpot_id=1,
            ambient_dim=2,
            source=places.auto_source,
            target=places.auto_target,
            qbx_forced_limit=qbx_forced_limit)

    dd = places.auto_target
    target_discr = places.get_discretization(dd.geometry, dd.discr_stage)
    dd = places.auto_source
    source_discr = places.get_discretization(dd.geometry, dd.discr_stage)

    index_set = _build_block_index(actx, source_discr, factor=0.6)
    index_set = MatrixBlockIndexRanges(ctx, index_set, index_set)

    from pytential.symbolic.execution import _prepare_expr
    op = _prepare_expr(places, op)

    # build full QBX matrix
    from pytential.symbolic.matrix import MatrixBuilder
    mbuilder = MatrixBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            context={})
    qbx_mat = mbuilder(op)

    # build full p2p matrix
    from pytential.symbolic.matrix import P2PMatrixBuilder
    mbuilder = P2PMatrixBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            context={})
    p2p_mat = mbuilder(op)

    assert p2p_mat.shape == (target_discr.ndofs, source_discr.ndofs)

    # build block qbx and p2p matrices
    from pytential.symbolic.matrix import NearFieldBlockBuilder
    mbuilder = NearFieldBlockBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            index_set=index_set,
            context={})
    mat = mbuilder(op)
    if dd.discr_stage is not None:
        assert _max_block_error(qbx_mat, mat, index_set.get(queue)) < 1.0e-14

    from pytential.symbolic.matrix import FarFieldBlockBuilder
    mbuilder = FarFieldBlockBuilder(actx,
            dep_expr=u_sym,
            other_dep_exprs=[],
            dep_source=places.get_geometry(dd.geometry),
            dep_discr=places.get_discretization(dd.geometry, dd.discr_stage),
            places=places,
            index_set=index_set,
            context={},
            exclude_self=True)
    mat = mbuilder(op)
    assert _max_block_error(p2p_mat, mat, index_set.get(queue)) < 1.0e-14


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from pytest import main
        main([__file__])

# vim: fdm=marker
