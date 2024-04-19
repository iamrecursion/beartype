#!/usr/bin/env python3
# --------------------( LICENSE                            )--------------------
# Copyright (c) 2014-2024 Beartype authors.
# See "LICENSE" for further details.

'''
**Beartype callable-based data validation unit tests.**

This submodule unit tests the subset of the public API of the
:mod:`beartype.vale` subpackage defined by the private
:mod:`beartype.vale._is._valeisargumentative` submodule.
'''


# ....................{ IMPORTS                            }....................
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# WARNING: To raise human-readable test errors, avoid importing from
# package-specific submodules at module scope.
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

# ....................{ TESTS ~ class : is                 }....................
def test_api_vale_is_argumentative_pass() -> None:
    '''
    Test successful usage of the :mod:`beartype.vale.Is` factory.
    '''

    from beartype import beartype
    from typing import TypeVar, TypeAlias, Annotated
    from beartype.vale import IsArgumentative
    from copy import deepcopy

    def my_check(out_list: list[int], in_list: list[int]) -> bool:
        print("OH GOD HELP ME")
        return len(out_list) == len(in_list) + 1

    Item = TypeVar("Item")
    ReturnType: TypeAlias = Annotated[
        list[Item],
        IsArgumentative[my_check]
    ]

    @beartype
    def append(in_list: list[Item], item: Item) -> ReturnType:
        new_list = deepcopy(in_list)
        new_list.append(item)
        return new_list

    append([1, 2], 3)

    # TODO Need to somehow reduce the arg placeholder generation to a no-op as needed.
    # `code_check_args` needs to generate the appropriate arg placeholders in scope, _in order_.

    # TODO Got to work out how to do argument dependencies here.
    # TODO Myriad tests including combinations with other validators


def test_api_vale_is_argumentative_fail() -> None:
    '''
    Test unsuccessful usage of the :mod:`beartype.vale.Is` factory.
    '''

    # TODO tests where arguments don't match up

