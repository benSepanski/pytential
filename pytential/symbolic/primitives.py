from __future__ import division, absolute_import

__copyright__ = "Copyright (C) 2010-2013 Andreas Kloeckner"

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

import six
from six.moves import intern
from warnings import warn

import numpy as np
from pymbolic.primitives import (  # noqa: F401,N813
        Expression as ExpressionBase, Variable as var,
        cse_scope as cse_scope_base,
        make_common_subexpression as cse)
from pymbolic.geometric_algebra import MultiVector, componentwise
from pymbolic.geometric_algebra.primitives import (  # noqa: F401
        NablaComponent, DerivativeSource, Derivative as DerivativeBase)
from pymbolic.primitives import make_sym_vector  # noqa: F401
from pytools.obj_array import make_obj_array, join_fields  # noqa: F401

from functools import partial


__doc__ = """
.. |where-blurb| replace:: A symbolic name for a
    :class:`pytential.discretization.Discretization`

Object types
^^^^^^^^^^^^
Based on the mathematical quantity being represented, the following types of
objects occur as part of a symbolic operator representation:

*   If a quantity is a scalar, it is just a symbolic expression--that is, a nested
    combination of placeholders (see below), arithmetic on them (see
    :mod:`pymbolic.primitives`. These objects are created simply by doing
    arithmetic on placeholders.

*   If the quantity is "just a bunch of scalars" (like, say, rows in a system
    of integral equations), the symbolic representation an object array. Each
    element of the object array contains a symbolic expression.

    :func:`pytools.obj_array.make_obj_array` and
    :func:`pytools.obj_array.join_fields`
    can help create those.

*   If it is a geometric quantity (that makes sense without explicit reference to
    coordinates), it is a :class:`pymbolic.geometric_algebra.MultiVector`.
    This can be converted to an object array by calling :
    :meth:`pymbolic.geometric_algebra.MultiVector.as_vector`.

:mod:`pyopencl.array.Array` instances do not occur on the symbolic of
:mod:`pymbolic` at all.  Those hold per-node degrees of freedom (and only
those), which is not visible as an array axis in symbolic code. (They're
visible only once evaluated.)

Placeholders
^^^^^^^^^^^^

.. autoclass:: var
.. autofunction:: make_sym_vector
.. autofunction:: make_sym_mv
.. autofunction:: make_sym_surface_mv

Functions
^^^^^^^^^

.. data:: real
.. data:: imag
.. data:: conj
.. data:: abs

.. data:: sqrt

.. data:: sin
.. data:: cos
.. data:: tan

.. data:: asin
.. data:: acos
.. data:: atan
.. data:: atan2

.. data:: sinh
.. data:: cosh
.. data:: tanh

.. data:: asinh
.. data:: acosh
.. data:: atanh

.. data:: exp
.. data:: log

Discretization properties
^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: QWeight
.. autofunction:: nodes
.. autofunction:: parametrization_derivative
.. autofunction:: parametrization_derivative_matrix
.. autofunction:: pseudoscalar
.. autofunction:: area_element
.. autofunction:: sqrt_jac_q_weight
.. autofunction:: normal
.. autofunction:: mean_curvature
.. autofunction:: first_fundamental_form
.. autofunction:: second_fundamental_form
.. autofunction:: shape_operator

.. autoclass:: DOFGranularityConverter
.. autofunction:: qbx_quad_resolution
.. autofunction:: qbx_expansion_radii
.. autofunction:: qbx_expansion_centers

Elementary numerics
^^^^^^^^^^^^^^^^^^^

.. autoclass:: NumReferenceDerivative
.. autoclass:: NodeSum
.. autoclass:: NodeMax
.. autoclass:: ElementwiseSum
.. autoclass:: ElementwiseMax
.. autofunction:: integral
.. autoclass:: Ones
.. autofunction:: ones_vec
.. autofunction:: area
.. autofunction:: mean
.. autoclass:: IterativeInverse

Operators
^^^^^^^^^

.. autoclass:: Interpolation

Geometric Calculus (based on Geometric/Clifford Algebra)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: Derivative

Conventional Calculus
^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: dd_axis
.. function:: d_dx
.. function:: d_dy
.. function:: d_dz
.. autofunction:: grad_mv
.. autofunction:: grad
.. autofunction:: laplace

Layer potentials
^^^^^^^^^^^^^^^^

.. autoclass:: IntG
.. autofunction:: int_g_dsource

.. autofunction:: S
.. autofunction:: Sp
.. autofunction:: Spp
.. autofunction:: D
.. autofunction:: Dp
.. autofunction:: normal_derivative
.. autofunction:: tangential_derivative

"Conventional" Vector Calculus
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: tangential_onb
.. autofunction:: xyz_to_tangential
.. autofunction:: tangential_to_xyz
.. autofunction:: project_to_tangential
.. autofunction:: cross
.. autofunction:: n_dot
.. autofunction:: n_cross
.. autofunction:: curl
.. autofunction:: pretty

Pretty-printing expressions
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pretty
"""


# {{{ 'where' specifiers

class DEFAULT_SOURCE:  # noqa
    pass


class DEFAULT_TARGET:  # noqa
    pass


class _QBXSource(object):
    """A symbolic 'where' specifier for the a density of a
    :attr:`pytential.qbx.QBXLayerPotentialSource`
    layer potential source identified by :attr:`where`.

    .. attribute:: where

        An identifier of a layer potential source, as used in
        :func:`pytential.bind`.

    .. note::

        This is not documented functionality and only intended for
        internal use.
    """

    def __init__(self, where):
        self.where = where

    def __hash__(self):
        return hash((type(self), self.where))

    def __eq__(self, other):
        return type(self) is type(other) and self.where == other.where

    def __ne__(self, other):
        return not self.__eq__(other)


class QBXSourceStage1(_QBXSource):
    """An explicit symbolic 'where' specifier for the
    :attr:`pytential.qbx.QBXLayerPotentialSource.density_discr`
    of the layer potential source identified by :attr:`where`.
    """


class QBXSourceStage2(_QBXSource):
    """A symbolic 'where' specifier for the
    :attr:`pytential.qbx.QBXLayerPotentialSource.stage2_density_discr`
    of the layer potential source identified by :attr:`where`.
    """


class QBXSourceQuadStage2(_QBXSource):
    """A symbolic 'where' specifier for the
    :attr:`pytential.qbx.QBXLayerPotentialSource.quad_stage2_density_discr`
    of the layer potential source identified by :attr:`where`.
    """


class DOMAIN_TAG(object):   # noqa
    """General domain specifier

    .. attribute:: tag
    """
    init_arg_names = ("tag",)

    def __init__(self, tag):
        self.tag = tag

    def __hash__(self):
        return hash((type(self), self.tag))

    def __eq__(self, other):
        return type(self) is type(other) and self.tag == other.tag

    def __ne__(self, other):
        return not self.__eq__(other)

    def __getinitargs__(self):
        return (self.tag,)

    def __repr__(self):
        if isinstance(self.tag, str):
            tag = self.tag
        else:
            tag = tag.__name__ if isinstance(tag, type) else type(tag).__name__

        return "{}({})".format(type(self).__name__, tag)


class QBX_DOMAIN_STAGE1(DOMAIN_TAG):    # noqa
    """Specifier for
    :attr:~pytential.qbx.QBXLayerPotentialSource.density_discr`."""
    pass


class QBX_DOMAIN_STAGE2(DOMAIN_TAG):    # noqa
    """Specifier for
    :attr:~pytential.qbx.QBXLayerPotentialSource.stage2_density_discr`."""
    pass


class QBX_DOMAIN_QUAD_STAGE2(DOMAIN_TAG):   # noqa
    """Specifier for
    :attr:~pytential.qbx.QBXLayerPotentialSource.quad_stage2_density_discr`."""
    pass


class QBX_DOF_NODE: # noqa
    """DOFs are per-source node."""
    pass


class QBX_DOF_CENTER:   # noqa
    """DOFs interleaved per expansion center."""
    pass


class QBX_DOF_ELEMENT:  # noqa
    """DOFs per discretization element."""
    pass


class DOFDescriptor(object):
    """A data structure specifying the meaning of a vector of degrees of freedom
    that is handled by :mod:`pytential` (a "DOF vector"). In particular, using
    :attr:`domain`, this data structure describes the geometric object on which
    the (scalar) function described by the DOF vector exists. Using
    :attr:`granularity`, the data structure describes how the geometric object
    is discretized (e.g. conventional nodal data, per-element scalars, etc.)

    .. attribute:: domain

        Describes the domain on which a DOF is defined. The domain contains
        an internal tag for which exact
        :class:`~pytential.source.PotentialSource`,
        :class:`~pytential.target.TargetBase` or
        :class:`~meshmode.discretization.Discretization` it refers to.
        Can be a generic :class:`QBX_DOMAIN` or one of
        :class:`QBX_DOMAIN_STAGE1`, :class:`QBX_DOMAIN_STAGE2` or
        :class:`QBX_DOMAIN_QUAD_STAGE2`.

    .. attribute:: granularity

        Describes the level of granularity of the DOF.
        Can be one of :class:`QBX_DOF_NODE`, :class:`QBX_DOF_CENTER` or
        :class:`QBX_DOF_ELEMENT`.

    """

    init_arg_names = ("domain", "granularity")

    def __init__(self, domain, granularity=None):
        if (domain == DEFAULT_SOURCE
                or domain == DEFAULT_TARGET
                or isinstance(domain, str)):
            domain = DOMAIN_TAG(domain)

        if granularity is None:
            granularity = QBX_DOF_NODE

        if not (isinstance(domain, DOMAIN_TAG)
                or isinstance(domain, QBX_DOMAIN_STAGE1)
                or isinstance(domain, QBX_DOMAIN_STAGE2)
                or isinstance(domain, QBX_DOMAIN_QUAD_STAGE2)):
            raise ValueError('unknown domain tag: {}'.format(domain))

        if not (granularity == QBX_DOF_NODE
                or granularity == QBX_DOF_CENTER
                or granularity == QBX_DOF_ELEMENT):
            raise ValueError('unknown granularity: {}'.format(granularity))

        self.domain = domain
        self.granularity = granularity

    def __hash__(self):
        return hash((type(self), self.domain, self.granularity))

    def __eq__(self, other):
        return (type(self) is type(other)
                and self.domain == other.domain
                and self.granularity == other.granularity)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __getinitargs__(self):
        return (self.domain, self.granularity)

    def __repr__(self):
        return '{}({}, {})'.format(
                type(self).__name__,
                repr(self.domain),
                self.granularity.__name__)


def as_dofdesc(desc):
    if isinstance(desc, DOFDescriptor):
        return desc
    return DOFDescriptor(desc)


# }}}


class cse_scope(cse_scope_base):  # noqa
    DISCRETIZATION = "pytential_discretization"


# {{{ helper functions

def array_to_tuple(ary):
    """This function is typically used to make :class:`numpy.ndarray`
    instances hashable by converting them to tuples.
    """

    if isinstance(ary, np.ndarray):
        return tuple(ary)
    else:
        return ary

# }}}


class Expression(ExpressionBase):
    def stringifier(self):
        from pytential.symbolic.mappers import StringifyMapper
        return StringifyMapper


def make_sym_mv(name, num_components):
    return MultiVector(make_sym_vector(name, num_components))


def make_sym_surface_mv(name, ambient_dim, dim, where=None):
    par_grad = parametrization_derivative_matrix(ambient_dim, dim, where)

    return sum(
            var("%s%d" % (name, i))
            * cse(MultiVector(vec), "tangent%d" % i, cse_scope.DISCRETIZATION)
            for i, vec in enumerate(par_grad.T))


class Function(var):
    def __call__(self, operand, *args, **kwargs):
        # If the call is handed an object array full of operands,
        # return an object array of the operator applied to each of the
        # operands.

        from pytools.obj_array import is_obj_array, with_object_array_or_scalar
        if is_obj_array(operand):
            def make_op(operand_i):
                return self(operand_i, *args, **kwargs)

            return with_object_array_or_scalar(make_op, operand)
        else:
            return var.__call__(self, operand, *args, **kwargs)


class EvalMapperFunction(Function):
    pass


class CLMathFunction(Function):
    pass


real = EvalMapperFunction("real")
imag = EvalMapperFunction("imag")
conj = EvalMapperFunction("conj")
abs = EvalMapperFunction("abs")

sqrt = CLMathFunction("sqrt")

sin = CLMathFunction("sin")
cos = CLMathFunction("cos")
tan = CLMathFunction("tan")

asin = CLMathFunction("asin")
acos = CLMathFunction("acos")
atan = CLMathFunction("atan")
atan2 = CLMathFunction("atan2")

sinh = CLMathFunction("sinh")
cosh = CLMathFunction("cosh")
tanh = CLMathFunction("tanh")

asinh = CLMathFunction("asinh")
acosh = CLMathFunction("acosh")
atanh = CLMathFunction("atanh")

exp = CLMathFunction("exp")
log = CLMathFunction("log")


class DiscretizationProperty(Expression):
    """A quantity that depends exclusively on the discretization (and has no
    further arguments.
    """

    init_arg_names = ("where",)

    def __init__(self, where=None):
        """
        :arg where: |where-blurb|
        """

        self.where = where

    def __getinitargs__(self):
        return (self.where,)


# {{{ discretization properties

class QWeight(DiscretizationProperty):
    """Bare quadrature weights (without Jacobians)."""

    mapper_method = intern("map_q_weight")


class NodeCoordinateComponent(DiscretizationProperty):

    init_arg_names = ("ambient_axis", "where")

    def __init__(self, ambient_axis, where=None):
        """
        :arg where: |where-blurb|
        """
        self.ambient_axis = ambient_axis
        DiscretizationProperty.__init__(self, where)

    def __getinitargs__(self):
        return (self.ambient_axis, self.where)

    mapper_method = intern("map_node_coordinate_component")


def nodes(ambient_dim, where=None):
    """Return a :class:`pymbolic.geometric_algebra.MultiVector` of node
    locations.
    """

    return MultiVector(
            make_obj_array([
                NodeCoordinateComponent(i, where)
                for i in range(ambient_dim)]))


class NumReferenceDerivative(DiscretizationProperty):
    """An operator that takes a derivative
    of *operand* with respect to the the element
    reference coordinates.
    """

    init_arg_names = ("ref_axes", "operand", "where")

    def __new__(cls, ref_axes=None, operand=None, where=None):
        # If the constructor is handed a multivector object, return an
        # object array of the operator applied to each of the
        # coefficients in the multivector.

        if isinstance(operand, np.ndarray):
            def make_op(operand_i):
                return cls(ref_axes, operand_i, where=where)

            return componentwise(make_op, operand)
        else:
            return DiscretizationProperty.__new__(cls)

    def __init__(self, ref_axes, operand, where=None):
        """
        :arg ref_axes: a :class:`tuple` of tuples indicating indices of
            coordinate axes of the reference element to the number of derivatives
            which will be taken.  For example, the value ``((0, 2), (1, 1))``
            indicates that Each axis must occur at most once. The tuple must be
            sorted by the axis index.

            May also be a singile integer *i*, which is viewed as equivalent
            to ``((i, 1),)``.
        :arg where: |where-blurb|
        """

        if isinstance(ref_axes, int):
            ref_axes = ((ref_axes, 1),)

        if not isinstance(ref_axes, tuple):
            raise ValueError("ref_axes must be a tuple")

        if tuple(sorted(ref_axes)) != ref_axes:
            raise ValueError("ref_axes must be sorted")

        if len(dict(ref_axes)) != len(ref_axes):
            raise ValueError("ref_axes must not contain an axis more than once")

        self.ref_axes = ref_axes

        self.operand = operand
        DiscretizationProperty.__init__(self, where)

    def __getinitargs__(self):
        return (self.ref_axes, self.operand, self.where)

    mapper_method = intern("map_num_reference_derivative")


def reference_jacobian(func, output_dim, dim, where=None):
    """Return a :class:`np.array` representing the Jacobian of a vector function
    with respect to the reference coordinates.
    """
    jac = np.zeros((output_dim, dim), np.object)

    for i in range(output_dim):
        func_component = func[i]
        for j in range(dim):
            jac[i, j] = NumReferenceDerivative(j, func_component, where)

    return jac


def parametrization_derivative_matrix(ambient_dim, dim, where=None):
    """Return a :class:`np.array` representing the derivative of the
    reference-to-global parametrization.
    """

    return cse(
            reference_jacobian(
                [NodeCoordinateComponent(i, where) for i in range(ambient_dim)],
                ambient_dim, dim, where=where),
            "pd_matrix", cse_scope.DISCRETIZATION)


def parametrization_derivative(ambient_dim, dim, where=None):
    """Return a :class:`pymbolic.geometric_algebra.MultiVector` representing
    the derivative of the reference-to-global parametrization.
    """

    par_grad = parametrization_derivative_matrix(ambient_dim, dim, where)

    from pytools import product
    return product(MultiVector(vec) for vec in par_grad.T)


def pseudoscalar(ambient_dim, dim=None, where=None):
    """
    Same as the outer product of all parametrization derivative columns.
    """
    if dim is None:
        dim = ambient_dim - 1

    return cse(
            parametrization_derivative(ambient_dim, dim, where)
            .project_max_grade(),
            "pseudoscalar", cse_scope.DISCRETIZATION)


def area_element(ambient_dim, dim=None, where=None):
    return cse(
            sqrt(pseudoscalar(ambient_dim, dim, where).norm_squared()),
            "area_element", cse_scope.DISCRETIZATION)


def sqrt_jac_q_weight(ambient_dim, dim=None, where=None):
    return cse(
            sqrt(
                area_element(ambient_dim, dim, where)
                * QWeight(where)),
            "sqrt_jac_q_weight", cse_scope.DISCRETIZATION)


def normal(ambient_dim, dim=None, where=None):
    """Exterior unit normals."""

    # Don't be tempted to add a sign here. As it is, it produces
    # exterior normals for positively oriented curves and surfaces.

    pder = (
            pseudoscalar(ambient_dim, dim, where)
            / area_element(ambient_dim, dim, where))
    return cse(
            # Dorst Section 3.7.2
            pder << pder.I.inv(),
            "normal",
            scope=cse_scope.DISCRETIZATION)


def mean_curvature(ambient_dim, dim=None, where=None):
    """(Numerical) mean curvature."""

    if dim is None:
        dim = ambient_dim - 1

    if ambient_dim == 2 and dim == 1:
        # https://en.wikipedia.org/wiki/Curvature#Local_expressions
        xp, yp = parametrization_derivative_matrix(ambient_dim, dim, where)

        xpp, ypp = cse(
                reference_jacobian([xp[0], yp[0]], ambient_dim, dim, where),
                "p2d_matrix", cse_scope.DISCRETIZATION)

        kappa = (xp[0]*ypp[0] - yp[0]*xpp[0]) / (xp[0]**2 + yp[0]**2)**(3/2)
    elif ambient_dim == 3 and dim == 2:
        # https://en.wikipedia.org/wiki/Mean_curvature#Surfaces_in_3D_space
        s_op = shape_operator(ambient_dim, dim=dim, where=where)
        kappa = -0.5 * sum(s_op[i, i] for i in range(s_op.shape[0]))
    else:
        raise NotImplementedError('not available in {}D for {}D surfaces'
                .format(ambient_dim, dim))

    return kappa


def first_fundamental_form(ambient_dim, dim=None, where=None):
    if dim is None:
        dim = ambient_dim - 1

    if ambient_dim != 3 and dim != 2:
        raise NotImplementedError("only available for surfaces in 3D")

    pd_mat = parametrization_derivative_matrix(ambient_dim, dim, where)

    return cse(
            np.dot(pd_mat.T, pd_mat),
            "fundform1")


def second_fundamental_form(ambient_dim, dim=None, where=None):
    """Compute the second fundamental form of a surface. This is in reference
    to the reference-to-global mapping in use for each element.

    .. note::

        Some references assume that the second fundamental form is computed
        with respect to an orthonormal basis, which this is not.
    """
    if dim is None:
        dim = ambient_dim - 1

    if ambient_dim != 3 and dim != 2:
        raise NotImplementedError("only available for surfaces in 3D")

    r = nodes(ambient_dim, where=where).as_vector()

    # https://en.wikipedia.org/w/index.php?title=Second_fundamental_form&oldid=821047433#Classical_notation

    from functools import partial
    d = partial(NumReferenceDerivative, where=where)
    ruu = d(((0, 2),), r)
    ruv = d(((0, 1), (1, 1)), r)
    rvv = d(((1, 2),), r)

    nrml = normal(ambient_dim, dim, where).as_vector()

    ff2_l = cse(np.dot(ruu, nrml), "fundform2_L")
    ff2_m = cse(np.dot(ruv, nrml), "fundform2_M")
    ff2_n = cse(np.dot(rvv, nrml), "fundform2_N")

    result = np.zeros((2, 2), dtype=object)
    result[0, 0] = ff2_l
    result[0, 1] = result[1, 0] = ff2_m
    result[1, 1] = ff2_n

    return result


def shape_operator(ambient_dim, dim=None, where=None):
    if dim is None:
        dim = ambient_dim - 1

    if ambient_dim != 3 and dim != 2:
        raise NotImplementedError("only available for surfaces in 3D")

    # https://en.wikipedia.org/w/index.php?title=Differential_geometry_of_surfaces&oldid=833587563
    (E, F), (F, G) = first_fundamental_form(ambient_dim, dim, where)
    (e, f), (f, g) = second_fundamental_form(ambient_dim, dim, where)

    result = np.zeros((2, 2), dtype=object)
    result[0, 0] = e*G-f*F
    result[0, 1] = f*G-g*F
    result[1, 0] = f*E-e*F
    result[1, 1] = g*E-f*F

    return cse(
            1/(E*G-F*F)*result,
            "shape_operator")


def _panel_size(ambient_dim, dim=None, where=None):
    # A broken quasi-1D approximation of 1D element size. Do not use.

    if dim is None:
        dim = ambient_dim - 1

    return ElementwiseSum(
            area_element(ambient_dim=ambient_dim, dim=dim)
            * QWeight())**(1/dim)


def _small_mat_inverse(mat):
    m, n = mat.shape
    if m != n:
        raise ValueError("inverses only make sense for square matrices")

    if m == 1:
        return make_obj_array([1/mat[0, 0]])
    elif m == 2:
        (a, b), (c, d) = mat
        return 1/(a*d-b*c) * make_obj_array([
            [d, -b],
            [-c, a],
            ])
    else:
        raise NotImplementedError(
                "inverse formula for %dx%d matrices" % (m, n))


def _small_mat_eigenvalues(mat):
    m, n = mat.shape
    if m != n:
        raise ValueError("eigenvalues only make sense for square matrices")

    if m == 1:
        return make_obj_array([mat[0, 0]])
    elif m == 2:
        (a, b), (c, d) = mat
        return make_obj_array([
                -(sqrt(d**2-2*a*d+4*b*c+a**2)-d-a)/2,
                 (sqrt(d**2-2*a*d+4*b*c+a**2)+d+a)/2
                ])
    else:
        raise NotImplementedError(
                "eigenvalue formula for %dx%d matrices" % (m, n))


def _equilateral_parametrization_derivative_matrix(ambient_dim, dim=None,
        where=None):
    if dim is None:
        dim = ambient_dim - 1

    pder_mat = parametrization_derivative_matrix(ambient_dim, dim, where)

    # The above procedure works well only when the 'reference' end of the
    # mapping is in equilateral coordinates.
    from modepy.tools import EQUILATERAL_TO_UNIT_MAP
    equi_to_unit = EQUILATERAL_TO_UNIT_MAP[dim].a

    # This is the Jacobian of the (equilateral reference element) -> (global) map.
    return cse(
            np.dot(pder_mat, equi_to_unit),
            "equilateral_pder_mat")


def _simplex_mapping_max_stretch_factor(ambient_dim, dim=None, where=None,
        with_elementwise_max=True):
    """Return the largest factor by which the reference-to-global
    mapping stretches the bi-unit (i.e. :math:`[-1,1]`) reference
    element along any axis.

    Returns a DOF vector that is elementwise constant.
    """

    if dim is None:
        dim = ambient_dim - 1

    # The 'technique' here is ad-hoc, but I'm fairly confident it's better than
    # what we had. The idea is that singular values of the mapping Jacobian
    # yield "stretch factors" of the mapping Why? because it maps a right
    # singular vector $`v_1`$ (of unit length) to $`\sigma_1 u_1`$, where
    # $`u_1`$ is the corresponding left singular vector (also of unit length).
    # And so the biggest one tells us about the direction with the 'biggest'
    # stretching, where 'stretching' (*2 to remove bi-unit reference element)
    # reflects available quadrature resolution in that direction.

    equi_pder_mat = _equilateral_parametrization_derivative_matrix(
            ambient_dim, dim, where)

    # Compute eigenvalues of J^T to compute SVD.
    equi_pder_mat_jtj = cse(
            np.dot(equi_pder_mat.T, equi_pder_mat),
            "pd_mat_jtj")

    stretch_factors = [
            cse(sqrt(s), "mapping_singval_%d" % i)
            for i, s in enumerate(
                _small_mat_eigenvalues(
                    # Multiply by 4 to compensate for equilateral reference
                    # elements of side length 2. (J^T J contains two factors of
                    # two.)
                    4 * equi_pder_mat_jtj))]

    from pymbolic.primitives import Max
    result = Max(tuple(stretch_factors))

    if with_elementwise_max:
        result = ElementwiseMax(result, where=where)

    return cse(result, "mapping_max_stretch", cse_scope.DISCRETIZATION)


def _max_curvature(ambient_dim, dim=None, where=None):
    # An attempt at a 'max curvature' criterion.

    if dim is None:
        dim = ambient_dim - 1

    if ambient_dim == 2:
        return abs(mean_curvature(ambient_dim, dim, where=where))
    elif ambient_dim == 3:
        shape_op = shape_operator(ambient_dim, dim, where=where)

        abs_principal_curvatures = [
                abs(x) for x in _small_mat_eigenvalues(shape_op)]
        from pymbolic.primitives import Max
        return cse(Max(tuple(abs_principal_curvatures)))
    else:
        raise NotImplementedError("curvature criterion not implemented in %d "
                "dimensions" % ambient_dim)


def _scaled_max_curvature(ambient_dim, dim=None, where=None):
    """An attempt at a unit-less, scale-invariant quantity that characterizes
    'how much curviness there is on an element'. Values seem to hover around 1
    on typical meshes. Empirical evidence suggests that elements exceeding
    a threshold of about 0.8-1 will have high QBX truncation error.
    """

    return _max_curvature(ambient_dim, dim, where=where) * \
            _simplex_mapping_max_stretch_factor(ambient_dim, dim, where=where,
                    with_elementwise_max=False)

# }}}


# {{{ qbx-specific geometry

class DOFGranularityConverter(Expression):
    """Converts a vector of DOFs to a vector of a desired granularity.

    .. attribute:: granularity

        New granularity of the DOF vector. Can be one of the following
        strings:

        - 'nsources': keeps the granularity of the DOF vector.
        - 'ncenters': for interleaved expansion centers.
        - 'npanels': per-element.
    """

    init_arg_names = ("granularity", "operand", "where")

    def __new__(cls, granularity, operand, where=None):
        if isinstance(operand, np.ndarray):
            def make_op(operand_i):
                return cls(granularity, operand_i, where=where)

            return componentwise(make_op, operand)
        else:
            return Expression.__new__(cls)

    def __init__(self, granularity, operand, where=None):
        self.granularity = granularity
        self.operand = operand
        self.where = where

    def __getinitargs__(self):
        return (self.granularity, self.operand, self.where)

    mapper_method = intern("map_dof_granularity_converter")


def qbx_quad_resolution(ambient_dim, granularity="npanels", where=None):
    stretch = _simplex_mapping_max_stretch_factor(ambient_dim, where=where)
    return DOFGranularityConverter(granularity, stretch, where=where)


def qbx_expansion_radii(factor, ambient_dim,
        granularity="nsources", where=None):
    """
    :arg factor: stick out factor for expansion radii.
    """

    stretch = _simplex_mapping_max_stretch_factor(ambient_dim, where=where)
    radii = DOFGranularityConverter(granularity, factor * stretch, where=where)

    return cse(radii, cse_scope.DISCRETIZATION)


def qbx_expansion_centers(factor, side, ambient_dim, dim=None, where=None):
    """
    :arg factor: target confinement factor for expansion radii.
    :arg side: `+1` or `-1` expansion side, relative to the direction of
        the normal vector.
    """

    x = nodes(ambient_dim, where=where)
    normals = normal(ambient_dim, dim=dim, where=where)
    radii = qbx_expansion_radii(factor, ambient_dim,
            granularity="nsources", where=where)

    centers = x + side * radii * normals

    return cse(centers.as_vector(), cse_scope.DISCRETIZATION)

# }}}


# {{{ operators

class SingleScalarOperandExpression(Expression):

    init_arg_names = ("operand",)

    def __new__(cls, operand=None):
        # If the constructor is handed a multivector object, return an
        # object array of the operator applied to each of the
        # coefficients in the multivector.

        if isinstance(operand, (np.ndarray, MultiVector)):
            def make_op(operand_i):
                return cls(operand_i)

            return componentwise(make_op, operand)
        else:
            return Expression.__new__(cls)

    def __init__(self, operand):
        self.operand = operand

    def __getinitargs__(self):
        return (self.operand,)


class NodeSum(SingleScalarOperandExpression):
    """Implements a global sum over all discretization nodes."""

    mapper_method = "map_node_sum"


class NodeMax(SingleScalarOperandExpression):
    """Implements a global maximum over all discretization nodes."""

    mapper_method = "map_node_max"


def integral(ambient_dim, dim, operand, where=None):
    """A volume integral of *operand*."""

    return NodeSum(
            area_element(ambient_dim, dim, where)
            * QWeight(where)
            * operand)


class SingleScalarOperandExpressionWithWhere(Expression):

    init_arg_names = ("operand", "where")

    def __new__(cls, operand=None, where=None):
        # If the constructor is handed a multivector object, return an
        # object array of the operator applied to each of the
        # coefficients in the multivector.

        if isinstance(operand, (np.ndarray, MultiVector)):
            def make_op(operand_i):
                return cls(operand_i, where)

            return componentwise(make_op, operand)
        else:
            return Expression.__new__(cls)

    def __init__(self, operand, where=None):
        self.operand = operand
        self.where = where

    def __getinitargs__(self):
        return (self.operand, self.where)


class ElementwiseSum(SingleScalarOperandExpressionWithWhere):
    """Returns a vector of DOFs with all entries on each element set
    to the sum of DOFs on that element.
    """

    mapper_method = "map_elementwise_sum"


class ElementwiseMin(SingleScalarOperandExpressionWithWhere):
    """Returns a vector of DOFs with all entries on each element set
    to the minimum of DOFs on that element.
    """

    mapper_method = "map_elementwise_min"


class ElementwiseMax(SingleScalarOperandExpressionWithWhere):
    """Returns a vector of DOFs with all entries on each element set
    to the maximum of DOFs on that element.
    """

    mapper_method = "map_elementwise_max"


class Ones(Expression):
    """A DOF-vector that is constant *one* on the whole
    discretization.
    """

    init_arg_names = ("where",)

    def __init__(self, where=None):
        self.where = where

    def __getinitargs__(self):
        return (self.where,)

    mapper_method = intern("map_ones")


def ones_vec(dim, where=None):
    from pytools.obj_array import make_obj_array
    return MultiVector(
                make_obj_array(dim*[Ones(where)]))


def area(ambient_dim, dim, where=None):
    return cse(integral(ambient_dim, dim, Ones(where), where), "area",
            cse_scope.DISCRETIZATION)


def mean(ambient_dim, dim, operand, where=None):
    return (
            integral(ambient_dim, dim, operand, where)
            / area(ambient_dim, dim, where))


class IterativeInverse(Expression):

    init_arg_names = ("expression", "rhs", "variable_name", "extra_vars", "where")

    def __init__(self, expression, rhs, variable_name, extra_vars={},
            where=None):
        self.expression = expression
        self.rhs = rhs
        self.variable_name = variable_name
        self.extra_vars = extra_vars
        self.where = where

    def __getinitargs__(self):
        return (self.expression, self.rhs, self.variable_name,
                self.extra_vars, self.where)

    def get_hash(self):
        return hash((self.__class__,) + (self.expression,
            self.rhs, self.variable_name,
            frozenset(six.iteritems(self.extra_vars)), self.where))

    mapper_method = intern("map_inverse")


class Derivative(DerivativeBase):
    @property
    def nabla(self):
        raise ValueError("Derivative.nabla should not be used"
                "--use Derivative.dnabla instead. (Note the extra 'd')"
                "To explain: 'nabla' was intended to be "
                "dimension-independent, which turned out to be a bad "
                "idea.")

    @staticmethod
    def resolve(expr):
        from pytential.symbolic.mappers import DerivativeBinder
        return DerivativeBinder()(expr)


def dd_axis(axis, ambient_dim, operand):
    """Return the derivative along (XYZ) axis *axis*
    (in *ambient_dim*-dimensional space) of *operand*.
    """
    from pytools.obj_array import is_obj_array, with_object_array_or_scalar
    if is_obj_array(operand):
        def dd_axis_comp(operand_i):
            return dd_axis(axis, ambient_dim, operand_i)

        return with_object_array_or_scalar(dd_axis_comp, operand)

    d = Derivative()

    unit_vector = np.zeros(ambient_dim)
    unit_vector[axis] = 1

    unit_mvector = MultiVector(unit_vector)

    return d.resolve(
            (unit_mvector.scalar_product(d.dnabla(ambient_dim)))
            * d(operand))


d_dx = partial(dd_axis, 0)
d_dy = partial(dd_axis, 1)
d_dz = partial(dd_axis, 2)


def grad_mv(ambient_dim, operand):
    """Return the *ambient_dim*-dimensional gradient of
    *operand* as a :class:`pymbolic.geometric_algebra.MultiVector`.
    """

    d = Derivative()
    return d.resolve(
            d.dnabla(ambient_dim) * d(operand))


def grad(ambient_dim, operand):
    """Return the *ambient_dim*-dimensional gradient of
    *operand* as a :class:`numpy.ndarray`.
    """
    return grad_mv(ambient_dim, operand).as_vector()


def laplace(ambient_dim, operand):
    d = Derivative()
    nabla = d.dnabla(ambient_dim)
    return d.resolve(nabla | d(
        d.resolve((nabla * d(operand))))).as_scalar()


# {{{ potentials

def hashable_kernel_args(kernel_arguments):
    hashable_args = []
    for key, val in sorted(kernel_arguments.items()):
        if isinstance(val, np.ndarray):
            val = tuple(val)
        hashable_args.append((key, val))

    return tuple(hashable_args)


class _NoArgSentinel(object):
    pass


class Interpolation(Expression):
    """Interpolate quantity from *source* to *target* discretization."""

    init_arg_names = ("source", "target", "operand")

    def __new__(cls, source, target, operand):
        if isinstance(operand, np.ndarray):
            def make_op(operand_i):
                return cls(source, target, operand_i)

            return componentwise(make_op, operand)
        else:
            return Expression.__new__(cls)

    def __init__(self, source, target, operand):
        self.source = source
        self.target = target
        self.operand = operand

    def __getinitargs__(self):
        return (self.source, self.target, self.operand)

    mapper_method = intern("map_interpolation")


class IntG(Expression):
    r"""
    .. math::

        \int_\Gamma g_k(x-y) \sigma(y) dS_y

    where :math:`\sigma` is *density*.
    """

    init_arg_names = ("kernel", "density", "qbx_forced_limit", "source", "target",
                      "kernel_arguments")

    def __new__(cls, kernel=None, density=None, *args, **kwargs):
        # If the constructor is handed a multivector object, return an
        # object array of the operator applied to each of the
        # coefficients in the multivector.

        if isinstance(density, (np.ndarray, MultiVector)):
            def make_op(operand_i):
                return cls(kernel, operand_i, *args, **kwargs)

            return componentwise(make_op, density)
        else:
            return Expression.__new__(cls)

    def __init__(self, kernel, density,
            qbx_forced_limit, source=None, target=None,
            kernel_arguments=None,
            **kwargs):
        """*target_derivatives* and later arguments should be considered
        keyword-only.

        :arg kernel: a kernel as accepted by
            :func:`sumpy.kernel.to_kernel_and_args`,
            likely a :class:`sumpy.kernel.Kernel`.
        :arg qbx_forced_limit: +1 if the output is required to originate from a
            QBX center on the "+" side of the boundary. -1 for the other side.
            Evaluation at a target with a value of +/- 1 in *qbx_forced_limit*
            will fail if no QBX center is found.

            +2 may be used to *allow* evaluation QBX center on the "+" side of the
            (but disallow evaluation using a center on the "-" side). Potential
            evaluation at the target still succeeds if no applicable QBX center
            is found. (-2 for the analogous behavior on the "-" side.)

            *None* may be used to avoid expressing a side preference for close
            evaluation.

            ``'avg'`` may be used as a shorthand to evaluate this potential
            as an average of the ``+1`` and the ``-1`` value.

        :arg kernel_arguments: A dictionary mapping named
            :class:`sumpy.kernel.Kernel` arguments
            (see :meth:`sumpy.kernel.Kernel.get_args`
            and :meth:`sumpy.kernel.Kernel.get_source_args`)
            to expressions that determine them

        :arg source: The symbolic name of the source discretization. This name
            is bound to a concrete :class:`pytential.source.LayerPotentialSourceBase`
            by :func:`pytential.bind`.

        :arg target: The symbolic name of the set of targets. This name gets
            assigned to a concrete target set by :func:`pytential.bind`.

        *kwargs* has the same meaning as *kernel_arguments* can be used as a
        more user-friendly interface.
        """

        if kernel_arguments is None:
            kernel_arguments = {}

        if isinstance(kernel_arguments, tuple):
            kernel_arguments = dict(kernel_arguments)

        from sumpy.kernel import to_kernel_and_args
        kernel, kernel_arguments_2 = to_kernel_and_args(kernel)

        for name, val in kernel_arguments_2.items():
            if name in kernel_arguments:
                raise ValueError("'%s' already set in kernel_arguments"
                        % name)
            kernel_arguments[name] = val

        del kernel_arguments_2

        if qbx_forced_limit not in [-1, +1, -2, +2, "avg", None]:
            raise ValueError("invalid value (%s) of qbx_forced_limit"
                    % qbx_forced_limit)

        kernel_arg_names = set(
                karg.loopy_arg.name
                for karg in (
                    kernel.get_args()
                    + kernel.get_source_args()))

        kernel_arguments = kernel_arguments.copy()
        if kwargs:
            for name, val in kwargs.items():
                if name in kernel_arguments:
                    raise ValueError("'%s' already set in kernel_arguments"
                            % name)

                if name not in kernel_arg_names:
                    raise TypeError("'%s' not recognized as kernel argument"
                            % name)

                kernel_arguments[name] = val

        provided_arg_names = set(kernel_arguments.keys())
        missing_args = kernel_arg_names - provided_arg_names
        if missing_args:
            raise TypeError("kernel argument(s) '%s' not supplied"
                    % ", ".join(missing_args))

        extraneous_args = provided_arg_names - kernel_arg_names
        if missing_args:
            raise TypeError("kernel arguments '%s' not recognized"
                    % ", ".join(extraneous_args))

        self.kernel = kernel
        self.density = density
        self.qbx_forced_limit = qbx_forced_limit
        self.source = source
        self.target = target
        self.kernel_arguments = kernel_arguments

    def copy(self, kernel=None, density=None, qbx_forced_limit=_NoArgSentinel,
            source=None, target=None, kernel_arguments=None):
        kernel = kernel or self.kernel
        density = density or self.density
        if qbx_forced_limit is _NoArgSentinel:
            qbx_forced_limit = self.qbx_forced_limit
        source = source or self.source
        target = target or self.target
        kernel_arguments = kernel_arguments or self.kernel_arguments
        return type(self)(kernel, density, qbx_forced_limit, source, target,
                kernel_arguments)

    def __getinitargs__(self):
        return (self.kernel, self.density, self.qbx_forced_limit,
                self.source, self.target,
                hashable_kernel_args(self.kernel_arguments))

    def __setstate__(self, state):
        # Overwrite pymbolic.Expression.__setstate__
        assert len(self.init_arg_names) == len(state), type(self)
        self.__init__(*state)

    mapper_method = intern("map_int_g")


_DIR_VEC_NAME = "dsource_vec"


def _insert_source_derivative_into_kernel(kernel):
    # Inserts the source derivative at the innermost
    # kernel wrapping level.
    from sumpy.kernel import DirectionalSourceDerivative

    if kernel.get_base_kernel() is kernel:
        return DirectionalSourceDerivative(
                kernel, dir_vec_name=_DIR_VEC_NAME)
    else:
        return kernel.replace_inner_kernel(
                _insert_source_derivative_into_kernel(kernel.inner_kernel))


def _get_dir_vec(dsource, ambient_dim):
    from pymbolic.mapper.coefficient import (
            CoefficientCollector as CoefficientCollectorBase)

    class _DSourceCoefficientFinder(CoefficientCollectorBase):
        def map_nabla_component(self, expr):
            return {expr: 1}

        def map_variable(self, expr):
            return {1: expr}

        def map_common_subexpression(self, expr):
            return {1: expr}

        def map_quotient(self, expr):
            return {1: expr}

    coeffs = _DSourceCoefficientFinder()(dsource)

    dir_vec = np.zeros(ambient_dim, np.object)
    for i in range(ambient_dim):
        dir_vec[i] = coeffs.pop(NablaComponent(i, None), 0)

    if coeffs:
        raise RuntimeError("source derivative expression contained constant term")

    return dir_vec


def int_g_dsource(ambient_dim, dsource, kernel, density,
            qbx_forced_limit, source=None, target=None,
            kernel_arguments=None, **kwargs):
    r"""
    .. math::

        \int_\Gamma \operatorname{dsource} \dot \nabla_y
            \dot g(x-y) \sigma(y) dS_y

    where :math:`\sigma` is *density*, and
    *dsource*, a multivector.
    Note that the first product in the integrand
    is a geometric product.

    .. attribute:: dsource

        A :class:`pymbolic.geometric_algebra.MultiVector`.
    """

    if kernel_arguments is None:
        kernel_arguments = {}

    if isinstance(kernel_arguments, tuple):
        kernel_arguments = dict(kernel_arguments)

    kernel = _insert_source_derivative_into_kernel(kernel)

    from pytools.obj_array import make_obj_array
    nabla = MultiVector(make_obj_array(
        [NablaComponent(axis, None)
            for axis in range(ambient_dim)]))

    def add_dir_vec_to_kernel_args(coeff):
        result = kernel_arguments.copy()
        result[_DIR_VEC_NAME] = _get_dir_vec(coeff, ambient_dim)
        return result

    density = cse(density)
    return (dsource*nabla).map(
            lambda coeff: IntG(
                kernel,
                density, qbx_forced_limit, source, target,
                kernel_arguments=add_dir_vec_to_kernel_args(coeff),
                **kwargs))

# }}}


# {{{ non-dimension-specific operators
#
# (these get made specific to dimensionality by
# pytential.symbolic.mappers.Dimensionalizer)

# {{{ geometric calculus


class _unspecified:  # noqa
    pass


def S(kernel, density,  # noqa
        qbx_forced_limit=_unspecified, source=None, target=None,
        kernel_arguments=None, **kwargs):

    if qbx_forced_limit is _unspecified:
        warn("not specifying qbx_forced_limit on call to 'S' is deprecated, "
                "defaulting to +1", stacklevel=2)
        qbx_forced_limit = +1

    return IntG(kernel, density, qbx_forced_limit, source, target,
            kernel_arguments, **kwargs)


def tangential_derivative(ambient_dim, operand, dim=None, where=None):
    pder = (
            pseudoscalar(ambient_dim, dim, where)
            / area_element(ambient_dim, dim, where))

    # FIXME: Should be formula (3.25) in Dorst et al.
    d = Derivative()
    return d.resolve(
            (d.dnabla(ambient_dim) * d(operand)) >> pder)


def normal_derivative(ambient_dim, operand, dim=None, where=None):
    d = Derivative()
    return d.resolve(
            (normal(ambient_dim, dim, where).scalar_product(d.dnabla(ambient_dim)))
            * d(operand))


def Sp(kernel, *args, **kwargs):  # noqa
    where = kwargs.get("target")
    if "qbx_forced_limit" not in kwargs:
        warn("not specifying qbx_forced_limit on call to 'Sp' is deprecated, "
                "defaulting to 'avg'", DeprecationWarning, stacklevel=2)
        kwargs["qbx_forced_limit"] = "avg"

    ambient_dim = kwargs.get("ambient_dim")
    from sumpy.kernel import Kernel
    if ambient_dim is None and isinstance(kernel, Kernel):
        ambient_dim = kernel.dim
    if ambient_dim is None:
        raise ValueError("ambient_dim must be specified, either through "
                "the kernel, or directly")
    dim = kwargs.pop("dim", None)

    return normal_derivative(
            ambient_dim,
            S(kernel, *args, **kwargs),
            dim=dim, where=where)


def Spp(kernel, *args, **kwargs):  # noqa
    ambient_dim = kwargs.get("ambient_dim")
    from sumpy.kernel import Kernel
    if ambient_dim is None and isinstance(kernel, Kernel):
        ambient_dim = kernel.dim
    if ambient_dim is None:
        raise ValueError("ambient_dim must be specified, either through "
                "the kernel, or directly")
    dim = kwargs.pop("dim", None)

    where = kwargs.get("target")
    return normal_derivative(
            ambient_dim,
            Sp(kernel, *args, **kwargs),
            dim=dim, where=where)


def D(kernel, *args, **kwargs):  # noqa
    ambient_dim = kwargs.get("ambient_dim")
    from sumpy.kernel import Kernel
    if ambient_dim is None and isinstance(kernel, Kernel):
        ambient_dim = kernel.dim
    if ambient_dim is None:
        raise ValueError("ambient_dim must be specified, either through "
                "the kernel, or directly")
    dim = kwargs.pop("dim", None)

    where = kwargs.get("source")

    if "qbx_forced_limit" not in kwargs:
        warn("not specifying qbx_forced_limit on call to 'D' is deprecated, "
                "defaulting to 'avg'", DeprecationWarning, stacklevel=2)
        kwargs["qbx_forced_limit"] = "avg"

    return int_g_dsource(
            ambient_dim,
            normal(ambient_dim, dim, where),
            kernel, *args, **kwargs).xproject(0)


def Dp(kernel, *args, **kwargs):  # noqa
    ambient_dim = kwargs.get("ambient_dim")
    from sumpy.kernel import Kernel
    if ambient_dim is None and isinstance(kernel, Kernel):
        ambient_dim = kernel.dim
    if ambient_dim is None:
        raise ValueError("ambient_dim must be specified, either through "
                "the kernel, or directly")
    dim = kwargs.pop("dim", None)
    target = kwargs.get("target")
    if "qbx_forced_limit" not in kwargs:
        warn("not specifying qbx_forced_limit on call to 'Dp' is deprecated, "
                "defaulting to +1", DeprecationWarning, stacklevel=2)
        kwargs["qbx_forced_limit"] = +1
    return normal_derivative(
            ambient_dim,
            D(kernel, *args, **kwargs),
            dim=dim, where=target)

# }}}

# }}}

# }}}


# {{{ conventional vector calculus

def tangential_onb(ambient_dim, dim=None, where=None):
    """Return a matrix of shape ``(ambient_dim, dim)`` with orthogonal columns
    spanning the tangential space of the surface of *where*.
    """

    if dim is None:
        dim = ambient_dim - 1

    pd_mat = parametrization_derivative_matrix(ambient_dim, dim, where)

    # {{{ Gram-Schmidt

    orth_pd_mat = np.zeros_like(pd_mat)
    for k in range(pd_mat.shape[1]):
        avec = pd_mat[:, k]
        q = avec
        for j in range(k):
            q = q - np.dot(avec, orth_pd_mat[:, j])*orth_pd_mat[:, j]
        q = cse(q, "q%d" % k)

        orth_pd_mat[:, k] = cse(q/sqrt(np.sum(q**2)), "orth_pd_vec%d_" % k)

    # }}}

    return orth_pd_mat


def xyz_to_tangential(xyz_vec, where=None):
    ambient_dim = len(xyz_vec)
    tonb = tangential_onb(ambient_dim, where=where)
    return make_obj_array([
        np.dot(tonb[:, i], xyz_vec)
        for i in range(ambient_dim - 1)
        ])


def tangential_to_xyz(tangential_vec, where=None):
    ambient_dim = len(tangential_vec) + 1
    tonb = tangential_onb(ambient_dim, where=where)
    return sum(
        tonb[:, i] * tangential_vec[i]
        for i in range(ambient_dim - 1))


def project_to_tangential(xyz_vec, where=None):
    return tangential_to_xyz(
            cse(xyz_to_tangential(xyz_vec, where), where))


def n_dot(vec, where=None):
    nrm = normal(len(vec), where).as_vector()

    return np.dot(nrm, vec)


def cross(vec_a, vec_b):
    assert len(vec_a) == len(vec_b) == 3

    from pytools import levi_civita
    from pytools.obj_array import make_obj_array
    return make_obj_array([
        sum(
            levi_civita((i, j, k)) * vec_a[j] * vec_b[k]
            for j in range(3) for k in range(3))
        for i in range(3)])


def n_cross(vec, where=None):
    return cross(normal(3, where).as_vector(), vec)


def div(vec):
    ambient_dim = len(vec)
    return sum(
            dd_axis(iaxis, ambient_dim, vec[iaxis])
            for iaxis in range(ambient_dim))


def curl(vec):
    from pytools import levi_civita
    from pytools.obj_array import make_obj_array

    return make_obj_array([
        sum(
            levi_civita((l, m, n)) * dd_axis(m, 3, vec[n])
            for m in range(3) for n in range(3))
        for l in range(3)])

# }}}


def pretty(expr):
    # Doesn't quite belong here, but this is exposed to the user as
    # "pytential.sym", so in here it goes.

    from pytential.symbolic.mappers import PrettyStringifyMapper
    stringify_mapper = PrettyStringifyMapper()
    from pymbolic.mapper.stringifier import PREC_NONE
    result = stringify_mapper(expr, PREC_NONE)

    splitter = "="*75 + "\n"

    cse_strs = stringify_mapper.get_cse_strings()
    if cse_strs:
        result = "\n".join(cse_strs)+"\n"+splitter+result

    return result

# vim: foldmethod=marker
