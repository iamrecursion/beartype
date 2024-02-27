#!/usr/bin/env python3
# --------------------( LICENSE                            )--------------------
# Copyright (c) 2014-2024 Beartype authors.
# See "LICENSE" for further details.

'''
**Beartype type-checking function code factories** (i.e., low-level
callables dynamically generating pure-Python code snippets type-checking
arbitrary objects passed to arbitrary callables against PEP-compliant type hints
passed to those same callables).

This private submodule is *not* intended for importation by downstream callers.
'''

# ....................{ IMPORTS                            }....................
from beartype.typing import (
    Callable,
    Optional,
)
from beartype._cave._cavemap import NoneTypeOr
from beartype._check.checkmagic import (
    ARG_NAME_CONF,
    ARG_NAME_GETRANDBITS,
    ARG_NAME_GET_VIOLATION,
    ARG_NAME_HINT,
    ARG_NAME_WARN,
    CODE_PITH_ROOT_NAME_PLACEHOLDER,
    FUNC_CHECKER_NAME_PREFIX,
)
from beartype._check.convert.convsanify import sanify_hint_root_statement
from beartype._check.code.codemake import make_check_expr
from beartype._check.error.errorget import (
    get_func_pith_violation,
    get_hint_object_violation,
)
from beartype._check.util.checkutilmake import make_func_signature
from beartype._check._checksnip import (
    CODE_CHECKER_SIGNATURE,
    CODE_RAISER_FUNC_PITH_CHECK_PREFIX,
    CODE_RAISER_HINT_OBJECT_CHECK_PREFIX,
    CODE_TESTER_CHECK_PREFIX,
    CODE_GET_FUNC_PITH_VIOLATION,
    CODE_GET_HINT_OBJECT_VIOLATION,
    CODE_GET_VIOLATION_CLS_STACK,
    CODE_GET_VIOLATION_RANDOM_INT,
    CODE_RAISE_VIOLATION,
    CODE_WARN_VIOLATION,
)
from beartype._conf.confcls import (
    BEARTYPE_CONF_DEFAULT,
    BeartypeConf,
)
from beartype._conf.conftest import die_unless_conf
from beartype._data.error.dataerrmagic import EXCEPTION_PLACEHOLDER
from beartype._data.func.datafuncarg import ARG_NAME_RETURN_REPR
from beartype._data.hint.datahinttyping import (
    CallableRaiser,
    CallableRaiserOrTester,
    CallableTester,
    CodeGenerated,
    LexicalScope,
    TypeStack,
)
from beartype._util.cache.utilcachecall import callable_cached
from beartype._util.error.utilerrraise import reraise_exception_placeholder
from beartype._util.error.utilerrwarn import (
    issue_warning,
    reissue_warnings_placeholder,
)
from beartype._util.func.utilfuncmake import make_func
from beartype._util.hint.pep.proposal.pep484585.utilpep484585ref import (
    get_hint_pep484585_ref_names_relative_to)
from beartype._util.hint.utilhinttest import is_hint_ignorable
from itertools import count
from warnings import (
    catch_warnings,
    warn,
)

# ....................{ FACTORIES ~ func                   }....................
@callable_cached
def make_func_raiser(
    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # CAUTION: All calls to this memoized factory pass parameters *POSITIONALLY*
    # rather than by keyword. Care should be taken when refactoring parameters,
    # particularly with respect to parameter position.
    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

    # Mandatory parameters.
    hint: object,

    # Optional parameters.
    conf: BeartypeConf = BEARTYPE_CONF_DEFAULT,
) -> CallableRaiser:
    '''
    **Type-checking raiser function factory** (i.e., low-level callable
    dynamically generating a pure-Python raiser function testing whether an
    arbitrary object passed to that tester satisfies the type hint passed to
    this factory and either raising an exception or emitting a warning when that
    object violates that hint).

    This factory is memoized for efficiency.

    Parameters
    ----------
    hint : object
        Type hint to be type-checked.
    conf : BeartypeConf, optional
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object). Defaults
        to ``BeartypeConf()``, the default :math:`O(1)` configuration.

    Returns
    -------
    CallableRaiser
        Type-checking raiser function generated by this factory for this hint.

    See Also
    --------
    :func:`._make_func_checker`
        Further details.
    '''

    # Defer to this lower-level factory function for ultimate lols.
    return _make_func_checker(  # type: ignore[return-value]
        hint=hint,
        conf=conf,
        make_code_check=make_code_raiser_hint_object_check,
    )


@callable_cached
def make_func_tester(
    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # CAUTION: All calls to this memoized factory pass parameters *POSITIONALLY*
    # rather than by keyword. Care should be taken when refactoring parameters,
    # particularly with respect to parameter position.
    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

    # Mandatory parameters.
    hint: object,

    # Optional parameters.
    conf: BeartypeConf = BEARTYPE_CONF_DEFAULT,
) -> CallableTester:
    '''
    **Type-checking tester function factory** (i.e., low-level callable
    dynamically generating a pure-Python tester function testing whether an
    arbitrary object passed to that tester satisfies the type hint passed to
    this factory and returning that result as its boolean return).

    This factory is memoized for efficiency.

    Parameters
    ----------
    hint : object
        Type hint to be type-checked.
    conf : BeartypeConf, optional
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object). Defaults
        to ``BeartypeConf()``, the default :math:`O(1)` configuration.

    Returns
    -------
    CallableTester
        Type-checking tester function generated by this factory for this hint.

    See Also
    --------
    :func:`._make_func_checker`
        Further details.
    '''

    # Defer to this lower-level factory function for great convenience.
    return _make_func_checker(  # type: ignore[return-value]
        hint=hint, conf=conf, make_code_check=make_code_tester_check)

# ....................{ FACTORIES ~ code                   }....................
#FIXME: Unit test us up, please.
@callable_cached
def make_code_tester_check(hint: object, conf: BeartypeConf) -> CodeGenerated:
    '''
    Pure-Python code snippet of a type-checking tester function type-checking an
    arbitrary object against the passed type hint under the passed beartype
    configuration by returning whether that object satisfies this hint or not.

    This factory is memoized for efficiency.

    Parameters
    ----------
    hint : object
        Type hint to be type-checked.
    conf : BeartypeConf
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object).

    Returns
    -------
    CodeGenerated
        Tuple containing the Python code snippet dynamically generated by this
        code factory and metadata describing that code. See the
        :attr:`beartype._data.hint.datahinttyping.CodeGenerated` type hint.

    See Also
    --------
    :func:`.make_check_expr`
        Further details.
    '''

    # Python code snippet comprising a single boolean expression type-checking
    # an arbitrary object against this hint.
    (
        code_expr,
        func_scope,
        hint_refs_type_basename,
    ) = make_check_expr(hint, conf)

    # Code snippet type-checking the root pith against the root hint.
    func_code = f'{CODE_TESTER_CHECK_PREFIX}{code_expr}'

    # Return all metadata required by higher-level callers.
    return (
        func_code,
        func_scope,
        hint_refs_type_basename,
    )

# ....................{ FACTORIES ~ code : raiser          }....................
#FIXME: Unit test us up, please.
@callable_cached
def make_code_raiser_func_pith_check(
    hint: object,
    conf: BeartypeConf,
    cls_stack: Optional[TypeStack],
    is_param: Optional[bool],
) -> CodeGenerated:
    '''
    Pure-Python code snippet of a type-checking raiser function type-checking a
    parameter or return of a decorated callable against the passed type hint
    under the passed beartype configuration by either raising a fatal exception
    *or* emitting a non-fatal warning when that parameter or return violates
    this hint.

    This factory is memoized for efficiency.

    Parameters
    ----------
    hint : object
        Type hint to be type-checked.
    conf : BeartypeConf
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object).
    cls_stack : Optional[TypeStack]
        **Type stack** (i.e., either a tuple of the one or more
        :func:`beartype.beartype`-decorated classes lexically containing the
        class variable or method annotated by this hint *or* :data:`None`).
        Defaults to :data:`None`.
    is_param : Optional[bool]
        **Tri-state pith boolean.** Although it would be simpler for this
        factory to accept a pith name, doing so would also effectively unmemoize
        this factory as well as all higher-level factories calling this factory.
        If the code snippet generated and returned by this factory is
        type-checking a previously localized:

        * Parameter of a decorated callable, :data:`True`.
        * Return of a decorated callable, :data:`False`.
        * Arbitrary object passed to the :func:`beartype.door.die_if_unbearable`
          type-checker, :data:`None`.

        Defaults to :data:`None`.

    Returns
    -------
    CodeGenerated
        Tuple containing the Python code snippet dynamically generated by this
        code factory and metadata describing that code. See the
        :attr:`beartype._data.hint.datahinttyping.CodeGenerated` type hint.

    See Also
    --------
    :func:`.make_check_expr`
        Further details.
    '''

    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # CAUTION: Synchronize with the make_code_hint_object_check() factory.
    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

    # Python code snippet comprising a single boolean expression type-checking
    # an arbitrary object against this hint.
    (
        code_expr,
        func_scope,
        hint_refs_type_basename,
    ) = make_check_expr(hint, conf, cls_stack)

    # Code snippet passing the value of the random integer previously generated
    # for the current call to the exception-handling function call embedded in
    # the "CODE_HINT_ROOT_SUFFIX" snippet, defaulting to *NOT* passing this.
    arg_random_int = (
        CODE_GET_VIOLATION_RANDOM_INT
        if ARG_NAME_GETRANDBITS in func_scope else
        ''
    )

    # Code snippet passing the current class stack if needed to type-check this
    # type hint, defaulting to *NOT* passing this.
    arg_cls_stack = CODE_GET_VIOLATION_CLS_STACK if cls_stack else ''

    # Pass hidden parameters to this raiser function exposing the
    # get_func_pith_violation() getter called by the
    # "CODE_GET_FUNC_PITH_VIOLATION" snippet.
    func_scope[ARG_NAME_GET_VIOLATION] = get_func_pith_violation

    # Code snippet generating a human-readable violation exception or warning
    # when the root pith violates the root type hint.
    code_get_violation = CODE_GET_FUNC_PITH_VIOLATION.format(
        arg_cls_stack=arg_cls_stack,
        arg_random_int=arg_random_int,
        pith_name=CODE_PITH_ROOT_NAME_PLACEHOLDER,
    )

    # Code snippet handling the previously generated violation by either raising
    # that violation as a fatal exception or emitting that violation as a
    # non-fatal warning.
    code_handle_violation = _make_code_raiser_violation(
        conf=conf, func_scope=func_scope, is_param=is_param)

    # Code snippet type-checking the root pith against the root hint.
    func_code = (
        f'{CODE_RAISER_FUNC_PITH_CHECK_PREFIX}'
        f'{code_expr}'
        f'{code_get_violation}'
        f'{code_handle_violation}'
    )

    # Return all metadata required by higher-level callers.
    return (
        func_code,
        func_scope,
        hint_refs_type_basename,
    )


@callable_cached
def make_code_raiser_func_pep484_noreturn_check(
    conf: BeartypeConf) -> CodeGenerated:
    '''
    Pure-Python code snippet of a type-checking raiser function type-checking a
    return of a decorated callable against the :obj:`typing.NoReturn` type hint
    annotating that return under the passed beartype configuration by either
    raising a fatal exception *or* emitting a non-fatal warning when that
    callable violates this hint by itself failing to raise an exception.

    This factory is memoized for efficiency.

    Parameters
    ----------
    conf : BeartypeConf
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object).

    Returns
    -------
    CodeGenerated
        Tuple containing the Python code snippet dynamically generated by this
        code factory and metadata describing that code. See the
        :attr:`beartype._data.hint.datahinttyping.CodeGenerated` type hint.
    '''

    # Lexical scope to be returned, initialized to the empty dictionary.
    func_scope = {}

    # Pass hidden parameters to this raiser function exposing the
    # get_func_pith_violation() getter called by the
    # "CODE_GET_FUNC_PITH_VIOLATION" snippet.
    func_scope[ARG_NAME_GET_VIOLATION] = get_func_pith_violation

    # Code snippet generating a human-readable violation exception or warning
    # when the root pith violates the root type hint.
    code_get_violation = CODE_GET_FUNC_PITH_VIOLATION.format(
        arg_cls_stack='',
        arg_random_int='',
        pith_name=ARG_NAME_RETURN_REPR,
    )

    # Code snippet handling the previously generated violation by either raising
    # that violation as a fatal exception or emitting that violation as a
    # non-fatal warning.
    code_handle_violation = _make_code_raiser_violation(
        conf=conf, func_scope=func_scope, is_param=False)

    # Code snippet type-checking the root pith against the root hint.
    func_code = f'{code_get_violation}{code_handle_violation}'

    # Return all metadata required by higher-level callers.
    return (
        func_code,
        func_scope,
        (),  # Irrelevant "hint_refs_type_basename" tuple item. Chug it!
    )


#FIXME: Unit test us up, please.
@callable_cached
def make_code_raiser_hint_object_check(
    hint: object, conf: BeartypeConf) -> CodeGenerated:
    '''
    Pure-Python code snippet of a type-checking raiser function type-checking an
    arbitrary object against the passed type hint under the passed beartype
    configuration by either raising a fatal exception *or* emitting a non-fatal
    warning when that object violates this hint.

    This factory is memoized for efficiency.

    Parameters
    ----------
    hint : object
        Type hint to be type-checked.
    conf : BeartypeConf
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object).

    Returns
    -------
    CodeGenerated
        Tuple containing the Python code snippet dynamically generated by this
        code factory and metadata describing that code. See the
        :attr:`beartype._data.hint.datahinttyping.CodeGenerated` type hint.

    See Also
    --------
    :func:`.make_check_expr`
        Further details.
    '''

    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # CAUTION: Synchronize with the make_code_raiser_func_pith_check() factory.
    #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

    # Python code snippet comprising a single boolean expression type-checking
    # an arbitrary object against this hint.
    (
        code_expr,
        func_scope,
        hint_refs_type_basename,
    ) = make_check_expr(hint, conf)

    # Code snippet passing the value of the random integer previously generated
    # for the current call to the exception-handling function call embedded in
    # the "CODE_HINT_ROOT_SUFFIX" snippet, defaulting to *NOT* passing this.
    arg_random_int = (
        CODE_GET_VIOLATION_RANDOM_INT
        if ARG_NAME_GETRANDBITS in func_scope else
        ''
    )

    # Pass hidden parameters to this raiser function exposing:
    # * The get_hint_object_violation() getter called by the
    #   "CODE_GET_HINT_OBJECT_VIOLATION" snippet.
    # * The passed type hint accessed by this snippet.
    func_scope[ARG_NAME_GET_VIOLATION] = get_hint_object_violation
    func_scope[ARG_NAME_HINT] = hint

    # Code snippet generating a human-readable violation exception or warning
    # when the root pith violates the root type hint.
    code_get_violation = CODE_GET_HINT_OBJECT_VIOLATION.format(
        arg_random_int=arg_random_int)

    # Code snippet handling the previously generated violation by either raising
    # that violation as a fatal exception or emitting that violation as a
    # non-fatal warning.
    code_handle_violation = _make_code_raiser_violation(
        conf=conf, func_scope=func_scope, is_param=None)

    # Code snippet type-checking the root pith against the root hint.
    func_code = (
        f'{CODE_RAISER_HINT_OBJECT_CHECK_PREFIX}'
        f'{code_expr}'
        f'{code_get_violation}'
        f'{code_handle_violation}'
    )

    # Return all metadata required by higher-level callers.
    return (
        func_code,
        func_scope,
        hint_refs_type_basename,
    )

# ....................{ PRIVATE ~ globals                  }....................
_func_checker_name_counter = count(start=0, step=1)
'''
**Type-checking function name uniquifier** (i.e., iterator yielding the next
integer incrementation starting at 0, leveraged by the
:func:`_make_func_checker` factory to uniquify the names of the type-checking
functions dynamically generated by that factory).
'''

# ....................{ PRIVATE ~ testers                  }....................
def _func_checker_ignorable(obj: object) -> bool:
    '''
    **Ignorable type-checking tester function singleton** (i.e., function
    unconditionally returning ``True``, semantically equivalent to a tester
    testing whether an arbitrary object passed to this tester satisfies an
    ignorable PEP-compliant type hint).

    The :func:`make_func_tester` factory efficiently returns this singleton when
    passed an ignorable type hint rather than inefficiently regenerating a
    unique ignorable type-checking tester function for that hint.
    '''

    return True

# ....................{ PRIVATE ~ factories : func         }....................
#FIXME: Unit test us up, please.
def _make_func_checker(
    # Mandatory parameters.
    hint: object,
    conf: BeartypeConf,
    make_code_check: Callable[..., CodeGenerated],

    # Optional parameters.
    exception_prefix: str = 'die_if_unbearable() or is_bearable() ',
) -> CallableRaiserOrTester:
    '''
    **Type-checking function factory** (i.e., low-level callable dynamically
    generating a pure-Python tester function testing whether an arbitrary object
    passed to that tester satisfies the type hint passed to this factory and
    either returning that result as its boolean return *or* raising a fatal
    exception or emitting a non-fatal warning if that result is :data:`False`).

    This factory is intentionally *not* memoized (e.g., by the
    ``@callable_cached`` decorator), as this factory is only called by
    higher-level memoized factories.

    Caveats
    -------
    **This factory intentionally accepts no** ``exception_cls`` **parameter.**
    Doing so would only ambiguously obscure context-sensitive exceptions raised
    by lower-level utility functions called by this higher-level factory.

    Parameters
    ----------
    hint : object
        Type hint to be type-checked.
    conf : BeartypeConf
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object).
    make_code_check : Callable[..., CodeGenerated]
        **Type-checking code factory** (i.e., function dynamically generating a
        code snippet of a function type-checking an arbitrary object against the
        passed type hint under the passed beartype configuration).
    exception_prefix : str, optional
        Human-readable substring prefixing the representation of this object in
        the exception message. Defaults to a reasonably sensible string.

    Returns
    -------
    CallableTester
        Type-checking tester function generated by this factory for this hint.

    Raises
    ------
    All exceptions raised by the lower-level :func:`.make_check_expr` factory.
    Additionally, this factory also raises:

    BeartypeConfException
        If this configuration is *not* a :class:`.BeartypeConf` instance.
    BeartypeDecorHintForwardRefException
        If this hint contains one or more relative forward references, which
        this factory explicitly prohibits to improve both the efficiency and
        portability of calls by users to the resulting type-checker.
    _BeartypeUtilCallableException
        If this function erroneously generates a syntactically invalid
        type-checking tester function. That should *never* happen, but let's
        admit that you're still reading this for a reason.

    Warns
    -----
    All warnings emitted by the lower-level :func:`.make_check_expr` factory.
    '''
    assert callable(make_code_check), f'{repr(make_code_check)} uncallable.'

    # Attempt to...
    try:
        # With a context manager "catching" *ALL* non-fatal warnings emitted
        # during this logic for subsequent "playrback" below...
        with catch_warnings(record=True) as warnings_issued:
            # ....................{ VALIDATION             }....................
            # If "conf" is *NOT* a configuration, raise an exception.
            die_unless_conf(conf)
            # Else, "conf" is a configuration.

            # Either:
            # * If this hint is PEP-noncompliant, the PEP-compliant type hint
            #   converted from this PEP-noncompliant type hint.
            # * If this hint is PEP-compliant and supported, this hint as is.
            # * Else, raise an exception (i.e., if this hint is neither
            #   PEP-noncompliant nor a supported PEP-compliant hint).
            #
            # Do this first *BEFORE* passing this hint to any further callables.
            hint = sanify_hint_root_statement(
                hint=hint, conf=conf, exception_prefix=EXCEPTION_PLACEHOLDER)

            # If this hint is ignorable, all objects satisfy this hint. In this
            # case, return a trivial function unconditionally returning true.
            if is_hint_ignorable(hint):
                return _func_checker_ignorable
            # Else, this hint is unignorable.

            # ....................{ CODE                   }....................
            # Python code snippet comprising a single boolean expression
            # type-checking an arbitrary object against this hint.
            (
                code_check,
                func_scope,
                hint_refs_type_basename,
            ) = make_code_check(hint, conf)

            # If this hint contains one or more relative forward references,
            # this hint is non-portable across lexical scopes. In this case,
            # raise an exception. Why? Because this hint is relative to and thus
            # valid only with respect to the caller's current lexical scope.
            # However, there is *NO* guarantee that the type-checking function
            # created and returned by this factory resides in the same lexical
            # scope.
            #
            # Suppose that type-checking function does, however. Even in that
            # best case, *ALL* calls to that tester would still be non-portable.
            # Why? Because those calls would now tacitly assume the original
            # lexical scope that they were called in. Those calls are now
            # lexically-dependent and thus could *NOT* be trivially
            # copy-and-pasted into different lexical scopes (e.g., submodules,
            # classes, or callables); doing so would raise exceptions at call
            # time, due to being unable to resolve those references. Preventing
            # users from doing something that will blow up in their test suites
            # commits after the fact is not simply a good thing; it's really the
            # only sane thing left.
            #
            # Suppose that we didn't particularly care about end user sanity,
            # however. Even in that worst case, resolving these references would
            # still be non-trivial, non-portable, and (perhaps most importantly)
            # incredibly slow. Why? Because doing so would require iteratively
            # introspecting the call stack for the first callable *NOT* residing
            # in the "beartype" codebase. These references would then be
            # resolved against the global and local lexical scope of that
            # callable. While technically feasible, doing so would render
            # higher-level "beartype" functions calling this lower-level factory
            # (e.g., our increasingly popular public beartype.door.is_bearable()
            # and die_if_unbearable() type-checkers) sufficiently slow as to be
            # pragmatically infeasible.
            if hint_refs_type_basename:
                # Defer to a low-level getter to raise a reasonable exception.
                get_hint_pep484585_ref_names_relative_to(
                    # First relative forward reference in this type hint,
                    # arbitrarily chosen for convenience.
                    hint=hint_refs_type_basename[0],
                    exception_prefix=(
                        f'{EXCEPTION_PLACEHOLDER}type hint {repr(hint)} '),
                )
            # Else, this hint contains *NO* relative forward references.

            # Unqualified basename of this type-checking function, uniquified by
            # suffixing an arbitrary integer unique to this function.
            func_checker_name = (
                f'{FUNC_CHECKER_NAME_PREFIX}{next(_func_checker_name_counter)}')

            # Python code snippet declaring the signature of the type-checking
            # function function to be defined and returned by this factory.
            code_signature = make_func_signature(
                func_name=func_checker_name,
                func_scope=func_scope,
                code_signature_format=CODE_CHECKER_SIGNATURE,
                conf=conf,
            )

            # Python code snippet defining this type-checking function in full.
            func_checker_code = f'{code_signature}{code_check}'

            # ....................{ FUNCTION               }....................
            # Type-checking tester function to be returned.
            func_tester = make_func(
                func_name=func_checker_name,
                func_code=func_checker_code,
                func_locals=func_scope,
                func_label='die_if_unbearable() or is_bearable() type-checker',
                is_debug=conf.is_debug,
            )
        # If one or more warnings were issued, reissue these warnings with each
        # placeholder substring (i.e., "EXCEPTION_PLACEHOLDER" instance)
        # replaced by a human-readable description of this callable and
        # annotated return.
        if warnings_issued:
            reissue_warnings_placeholder(
                warnings=warnings_issued, target_str=exception_prefix)
        # Else, *NO* warnings were issued.
    # If doing so raises *ANY* exception, reraise this exception with each
    # placeholder substring (i.e., "EXCEPTION_PLACEHOLDER" instance) replaced by
    # an explanatory prefix.
    except Exception as exception:
        reraise_exception_placeholder(
            exception=exception, target_str=exception_prefix)

    # Return this tester function.
    return func_tester  # type: ignore[return-value]

# ....................{ PRIVATE ~ factories : code         }....................
def _make_code_raiser_violation(
    # Mandatory parameters.
    conf: BeartypeConf,
    func_scope: LexicalScope,

    # Optional parameters.
    is_param: Optional[bool] = None,
) -> str:
    '''
    Pure-Python code snippet of a **type-checking raiser function** (i.e.,
    dynamically generated by the :func:`.make_raiser_func` factory) either
    raising a fatal exception or emitting a non-fatal warning when an arbitrary
    object violates an arbitrary type hint under the passed beartype
    configuration in the body of that raiser.

    This factory is intentionally *not* memoized (e.g., by the
    ``@callable_cached`` decorator), as this factory is only called by
    higher-level memoized factories.

    Parameters
    ----------
    conf : BeartypeConf
        **Beartype configuration** (i.e., self-caching dataclass encapsulating
        all settings configuring type-checking for the passed object).
    func_scope : LexicalScope
        **Lexical scope** (i.e., dictionary mapping from the relative
        unqualified name to value of each locally or globally scoped attribute
        accessible to a callable or class).
    is_param : Optional[bool]
        **Tri-state pith boolean.** Although it would be simpler for this
        factory to accept a pith name, doing so would also effectively unmemoize
        this factory as well as all higher-level factories calling this factory.
        If the code snippet generated and returned by this factory is
        type-checking a previously localized:

        * Parameter of a decorated callable, :data:`True`.
        * Return of a decorated callable, :data:`False`.
        * Arbitrary object passed to the :func:`beartype.door.die_if_uncallable`
          type-checker, :data:`None`.

        Defaults to :data:`None`.

    Returns
    -------
    CodeGenerated
        Tuple containing the Python code snippet dynamically generated by this
        code factory and metadata describing that code. See the
        :attr:`beartype._data.hint.datahinttyping.CodeGenerated` type hint for
        details.

    Raises
    ------
    All exceptions raised by the lower-level :func:`make_check_expr` factory.

    Warns
    -----
    All warnings emitted by the lower-level :func:`make_check_expr` factory.

    See Also
    --------
    :func:`.make_check_expr`
        Further details.
    '''
    assert isinstance(conf, BeartypeConf), f'{repr(conf)} not configuration.'
    assert isinstance(func_scope, dict), (
        f'{repr(func_scope)} not dictionary.')
    assert isinstance(is_param, NoneTypeOr[bool]), (
        f'{repr(is_param)} neither boolean nor "None".')

    # Pass a hidden parameter to this raiser function exposing the passed
    # beartype configuration accessed by this snippet.
    func_scope[ARG_NAME_CONF] = conf

    # Code snippet handling the previously generated violation by either raising
    # that violation as a fatal exception or emitting that violation as a
    # non-fatal warning, contextually initialized below.
    code_violation = ''  # type: ignore[assignment]

    # If this code snippet produces this violation by emitting a non-fatal
    # warning (rather than raising an exception), detected as either...
    if (
        # If this object is neither a parameter nor return of a decorated
        # callable, this object was directly passed to either the
        # beartype.door.is_bearable() or beartype.door.die_if_unbearable()
        # functions. In either case, set this boolean to this previously
        # computed DOOR-specific boolean.
        conf._is_violation_door_warn if is_param is None else
        # Else, this object is either a parameter or return of a decorated
        # callable.
        #
        # If this object is be a parameter of a decorated callable, set this
        # boolean to this previously computed parameter-specific boolean.
        conf._is_violation_param_warn if is_param else
        # Else, this object is *NOT* a parameter of a decorated callable. In this
        # case, this object *MUST* be a return of a decorated callable. Set
        # this boolean to this previously computed return-specific boolean.
        conf._is_violation_return_warn
    ):
        # Emit a non-fatal warning.
        code_violation = CODE_WARN_VIOLATION

        # Pass the warnings.warn() function required to emit this warning to
        # this wrapper function as an optional hidden parameter.
        #
        # Note that we intentionally do *NOT* pass the higher-level
        # issue_warning() function. Why? Efficiency, mostly. Recall that
        # issue_warning() is *ONLY* called to pretend that warnings generated by
        # callables both defined by and residing in this codebase are actually
        # generated by external third-party code. Although this wrapper function
        # is also generated by callables defined by this codebase (including
        # this callable, of course), this wrapper function does *NOT* reside
        # inside this codebase but instead effectively resides inside the
        # external third-party module defining the original function this
        # wrapper function wraps. Needlessly passing issue_warning() rather than
        # warn() here would only consume CPU cycles for *NO* tangible gain.
        func_scope[ARG_NAME_WARN] = warn
    # Else...
    else:
        # Raise a fatal exception.
        code_violation = CODE_RAISE_VIOLATION

    # Return this code snippet.
    return code_violation
