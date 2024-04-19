#!/usr/bin/env python3
# --------------------( LICENSE                            )--------------------
# Copyright (c) 2014-2024 Beartype authors.
# See "LICENSE" for further details.

'''
Beartype **functional validation classes** (i.e., :mod:`beartype`-specific
classes enabling callers to define PEP-compliant validators from arbitrary
caller-defined callables *not* efficiently generating stack-free code).

This private submodule defines the core low-level class hierarchy driving the
entire :mod:`beartype` validation ecosystem.

This private submodule is *not* intended for importation by downstream callers.
'''
from beartype._util.func.arg.utilfuncargget import get_func_arg_names
# ....................{ IMPORTS                            }....................
from beartype.roar import (
    BeartypeValeLambdaWarning,
    BeartypeValeValidationException,
)
from beartype.typing import Protocol
from beartype.vale._is._valeisabc import _BeartypeValidatorFactoryABC
from beartype.vale._core._valecore import BeartypeValidator
from beartype.vale._util._valeutilfunc import die_unless_unary_validator_tester, die_unless_validator_tester
from beartype.vale._util._valeutiltyping import BeartypeValidatorTester, BeartypeMultiArgValidatorTester
from beartype._data.hint.datahinttyping import LexicalScope
from beartype._util.func.utilfuncscope import add_func_scope_attr
from beartype._util.text.utiltextrepr import (
    represent_func,
    represent_object,
)

# ....................{ PRIVATE ~ protocols                }....................
class _SupportsBool(Protocol):
    '''
    Fast caching protocol matching any object whose class defines the
    :meth:`__bool__` dunder method.
    '''

    def __bool__(self) -> bool: ...


class _SupportsLen(Protocol):
    '''
    Fast caching protocol matching any object whose class defines the
    :meth:`__len__` dunder method.
    '''

    def __len__(self) -> bool: ...


_BoolLike = (_SupportsBool, _SupportsLen)
'''
:func:`isinstance`-able tuple of fast caching protocols matching any
**bool-like** (i.e., object whose class defines at least one of the
:meth:`__bool__` and/or :meth:`__len__` dunder methods).
'''

# ....................{ PRIVATE ~ subclasses               }....................
class _IsArgumentativeFactory(_BeartypeValidatorFactoryABC):
    # ..................{ DUNDERS                            }..................
    def __getitem__(  # type: ignore[override]
        self, is_valid: BeartypeMultiArgValidatorTester) -> BeartypeValidator:

        # ..................{ VALIDATE                       }..................
        # If this class was subscripted by either no arguments *OR* two or more
        # arguments, raise an exception.
        self._die_unless_getitem_args_1(is_valid)
        # Else, this class was subscripted by exactly one argument.

        # If that callable is *NOT* a validator tester, raise an exception.
        die_unless_validator_tester(is_valid)
        # Else, that callable is a validator tester.

        # We need to know the names of the additional arguments being passed to
        # the validator function.
        all_args = get_func_arg_names(is_valid)
        target_arg = all_args[0]
        remaining_args = all_args[1:]

        # Lambda function dynamically generating the machine-readable
        # representation of this validator, deferred due to the computational
        # expense of accurately retrieving the source code for this validator
        # (especially when this validator is itself a lambda function).
        get_repr = lambda: (
            f'{self._basename}['
            f'{represent_func(func=is_valid, warning_cls=BeartypeValeLambdaWarning)}'
            f']'
        )

        # ..................{ VALIDATOR                      }..................
        # Dictionary mapping from the name to value of each local attribute
        # referenced in the "is_valid_code" snippet defined below.
        is_valid_code_locals: LexicalScope = {}

        # Name of a new parameter added to the signature of each
        # @beartype-decorated wrapper function whose value is this validator,
        # enabling this validator to be called directly in the body of those
        # functions *WITHOUT* imposing additional stack frames.
        is_valid_attr_name = add_func_scope_attr(
            attr=is_valid, func_scope=is_valid_code_locals)

        def generate_additional_arg_string(arg_names: list[str]) -> str:
            def arg_p() -> str:
                return f"{arg_names[0]}"

            if len(arg_names) == 0:
                return ""
            elif len(arg_names) == 1:
                return f", {arg_p()}"
            else:
                return f", {arg_p()}{generate_additional_arg_string(arg_names[1:])}"

        is_valid_code_header = f"{is_valid_attr_name}({{obj}}"
        is_valid_code_args = generate_additional_arg_string(remaining_args)
        is_valid_code_footer = f")"
        is_valid_code = is_valid_code_header + is_valid_code_args + is_valid_code_footer

        # TODO somewhere in here I need to generate placeholders for arguments.

        # One one-liner to rule them all and in "pdb" bind them.
        return BeartypeValidator(
            is_valid=is_valid,  # TODO: The bool-ish conversion above and attribute mapping
            # Python code snippet calling this validator (via this new
            # parameter), passed an object to be interpolated into this snippet
            # by downstream logic.
            is_valid_code=is_valid_code,
            is_valid_code_locals=is_valid_code_locals,
            get_repr=get_repr,
            is_valid_target_arg_name=target_arg,
            is_valid_remaining_arg_names=remaining_args
        )
