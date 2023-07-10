#!/usr/bin/env python3
# --------------------( LICENSE                            )--------------------
# Copyright (c) 2014-2023 Beartype authors.
# See "LICENSE" for further details.

'''
Beartype **abstract syntax tree (AST) transformers** (i.e., low-level classes
instrumenting well-typed third-party modules with runtime type-checking
dynamically generated by the :func:`beartype.beartype` decorator).

This private submodule is *not* intended for importation by downstream callers.
'''

# ....................{ TODO                               }....................
#FIXME: [PEP 484] Additionally define:
#* Generator transformers. The idea here is that @beartype *CAN* actually
#  automatically type-check generator yields, sends, and returns at runtime.
#  How? By automatically injecting appropriate die_if_unbearable() calls
#  type-checking the values to be yielded, sent, and returned against the
#  appropriate type hints of the current generator factory *BEFORE* yielding,
#  sending, and returning those values. Shockingly, typeguard already does this
#  -- which is all manner of impressive. See the
#  TypeguardTransformer._use_memo() context manager for working code. Wow!
#
#See also:
#    https://github.com/agronholm/typeguard/blob/master/src/typeguard/_transformer.py

#FIXME: [SPEED] Consider generalizing the BeartypeNodeTransformer.__new__()
#class method to internally cache and return "BeartypeNodeTransformer" instances
#depending on the passed "conf_beartype" parameter. In general, most codebases
#will only leverage a single @beartype configuration (if any @beartype
#configuration at all); ergo, caching improves everything by enabling us to
#reuse the same "BeartypeNodeTransformer" instance for every hooked module.
#Score @beartype!
#
#See the BeartypeConf.__new__() method for relevant logic. \o/
#FIXME: Oh, wait. We probably do *NOT* want to cache -- at least, not within
#defining a comparable reinit() method as we do for "BeartypeCall". After
#retrieving a cached "BeartypeNodeTransformer" instance, we'll need to
#immediately call BeartypeNodeTransformer.reinit() to reinitialize that
#instance.
#
#This is all feasible, of course -- but let's just roll with the naive
#implementation for now, please.

#FIXME: [PEP 675] *OMG.* See also the third-party "executing" Python package:
#    https://github.com/alexmojaki/executing
#
#IPython itself internally leverages "executing" via "stack_data" (i.e., a
#slightly higher-level third-party Python package that internally leverages
#"executing") to syntax-highlight the currently executing AST node. Indeed,
#"executing" sports an intense test suite (much like ours) effectively
#guaranteeing a one-to-one mapping between stack frames and AST nodes.
#
#So, what's the Big Idea here? The Big Idea here is that @beartype can
#internally (...possibly only optionally, but possibly mandatorily) leverage
#"executing" to begin performing full-blown static type-checking at runtime --
#especially of mission critical type hints like "typing.LiteralString" which can
#*ONLY* be type-checked via static analysis. :o
#
#So, what's the Little Idea here? The Little Idea here is that @beartype can
#generate type-checking wrappers that type-check parameters or returns annotated
#by "typing.LiteralString" by calling an internal private utility function --
#say, "_die_unless_literalstring(func: Callable, arg_name: str) -> None" -- with
#"func" as the current type-checking wrapper and "arg_name" as either the name
#of that parameter or "return". The _die_unless_literalstring() raiser then:
#* Dynamically searches up the call stack for the stack frame encapsulating an
#  external call to the passed "func" callable.
#* Passes that stack frame to the "executing" package.
#* "executing" then returns the AST node corresponding to that stack frame.
#* Introspects that node for the passed parameter whose name is "arg_name".
#* Raises an exception unless the value of that parameter is an AST node
#  corresponding to a string literal.
#
#Of course, that won't necessarily be fast -- but it will be accurate. Since
#security trumps speed, speed is significantly less of a concern insofar as
#"typing.LiteralString" is concerned. Of course, we should also employ
#significant caching... if we even can.
#FIXME: Actually, while demonstrably awesome, even the above fails to suffice to
#to statically type-check "typing.LiteralString". We failed to fully read PEP
#675, which contains a section on inference. In the worst case, nothing less
#than a complete graph of the entire app and all transitive dependencies thereof
#suffices to decide whether a parameter satisfies "typing.LiteralString".
#
#Thankfully, the above idea generalizes from "typing.LiteralString" to other
#fascinating topics as well. Indeed, given sufficient caching, one could begin
#to internally generate and cache a mypy-like graph network whose nodes are
#typed attributes and whose edges are relations between those typed attributes.

# ....................{ IMPORTS                            }....................
from ast import (
    AST,
    AnnAssign,
    Call,
    ClassDef,
    # Constant,
    Expr,
    ImportFrom,
    Module,
    Name,
    NodeTransformer,
    Str,
    # Subscript,
    # alias,
    # expr,
    # keyword,
)
from beartype.claw._clawmagic import (
    NODE_CONTEXT_LOAD,
    BEARTYPE_CLAW_STATE_MODULE_NAME,
    BEARTYPE_CLAW_STATE_SOURCE_ATTR_NAME,
    BEARTYPE_CLAW_STATE_TARGET_ATTR_NAME,
    BEARTYPE_DECORATOR_MODULE_NAME,
    BEARTYPE_DECORATOR_SOURCE_ATTR_NAME,
    BEARTYPE_DECORATOR_TARGET_ATTR_NAME,
    BEARTYPE_RAISER_MODULE_NAME,
    BEARTYPE_RAISER_SOURCE_ATTR_NAME,
    BEARTYPE_RAISER_TARGET_ATTR_NAME,
)
from beartype.claw._ast._clawastmunge import (
    decorate_node,
    make_node_keyword_conf,
)
from beartype.claw._clawtyping import (
    NodeCallable,
    NodeT,
    NodeVisitResult,
)
from beartype.typing import (
    List,
    Optional,
)
from beartype._conf.confcls import (
    BEARTYPE_CONF_DEFAULT,
    BeartypeConf,
)
# from beartype._util.ast.utilastget import get_node_repr_indented
from beartype._util.ast.utilastmake import make_node_importfrom
from beartype._util.ast.utilastmunge import copy_node_metadata
from beartype._util.ast.utilasttest import is_node_callable_typed

# ....................{ SUBCLASSES                         }....................
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# CAUTION: To improve forward compatibility with the superclass API over which
# we have *NO* control, avoid accidental conflicts by suffixing *ALL* private
# and public attributes of this subclass by "_beartype".
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

#FIXME: Unit test us up, please.
class BeartypeNodeTransformer(NodeTransformer):
    '''
    **Beartype abstract syntax tree (AST) node transformer** (i.e., visitor
    pattern recursively transforming the AST tree passed to the :meth:`visit`
    method by decorating all typed callables and classes by the
    :func:`beartype.beartype` decorator).

    Design
    ----------
    This class was largely designed by reverse-engineering the standard
    :mod:`ast` module using the following code snippet. When run as the body of
    a script from the command line (e.g., ``python3 {muh_script}.py``), this
    snippet pretty-prints the desired target AST subtree implementing the
    desired source code (specified in this snippet via the ``CODE`` global). In
    short, this snippet trivializes the definition of arbitrarily complex
    AST-based code from arbitrarily complex Python code:

    .. code-block:: python

       import ast

       # Arbitrary desired code to pretty-print the AST representation of.
       CODE = """
       from beartype import beartype
       from beartype._conf.confcache import beartype_conf_id_to_conf

       @beartype(conf=beartype_conf_id_to_conf[139870142111616])
       def muh_func(): pass
       """

       # Dismantled, this is:
       # * "indent=...", producing pretty-printed (i.e., indented) output.
       # * "include_attributes=True", enabling pseudo-nodes (i.e., nodes lacking
       #   associated code metadata) to be distinguished from standard nodes
       #   (i.e., nodes having such metadata).
       print(ast.dump(ast.parse(CODE), indent=4, include_attributes=True))

    Attributes
    ----------
    _conf_beartype : BeartypeConf
        **Beartype configuration** (i.e., dataclass configuring the
        :mod:`beartype.beartype` decorator for *all* decoratable objects
        recursively decorated by this node transformer).
    _node_stack_beartype : List[AST]
        **Current visitation stack** (i.e., list of the zero or more parent
        nodes of the current node being visited by this node transformer), such
        that:

        * The first node on this stack is the **module node** encapsulating the
          module currently being visited by this node transformer.
        * The last node on this stack is the **parent node** of the current node
          being visited (and possibly transformed) by this node transformer.

        Note that the principal purpose of this stack is to deterministically
        differentiate the two different types of callables, each of which
        requires a correspondingly different type of decoration. These are:

        * Nodes encapsulating pure-Python **functions**, which this transformer
          directly decorates by the :func:`beartype.beartype` decorator.
        * Nodes encapsulating pure-Python **methods**, which this transformer
          does *not* directly decorate by that decorator. Why? Because this
          transformer already decorates classes by that decorator, which then
          implicitly decorates *all* methods defined by those classes.
          Needlessly re-decorating the same methods by the same decorator only
          harms runtime efficiency for no tangible gain.

    See Also
    ----------
    https://github.com/agronholm/typeguard/blob/fe5b578595e00d5e45c3795d451dcd7935743809/src/typeguard/importhook.py
        Last commit of the third-party Python package whose
        ``@typeguard.importhook.TypeguardTransformer`` class implements import
        hooks performing runtime type-checking in a similar manner, strongly
        inspiring this implementation.

        Note that all subsequent commits to that package generalize those import
        hooks into something else entirely, which increasingly resembles a
        static type-checker run at runtime; while fascinating and almost
        certainly ingenious, those commits are sufficiently inscrutable,
        undocumented, and unintelligible to warrant caution. Nonetheless, thanks
        so much to @agronholm (Alex Grönholm) for his pulse-pounding innovations
        in this burgeoning field! Our AST transformer is for you, @agronholm.
    '''

    # ..................{ INITIALIZERS                       }..................
    def __init__(
        self,

        # Mandatory keyword-only parameters.
        *,
        conf_beartype: BeartypeConf,
    ) -> None:
        '''
        Initialize this node transformer.

        Parameters
        ----------
        conf_beartype : BeartypeConf
            **Beartype configuration** (i.e., dataclass configuring the
            :mod:`beartype.beartype` decorator for *all* decoratable objects
            recursively decorated by this node transformer).
        '''
        assert isinstance(conf_beartype, BeartypeConf), (
            f'{repr(conf_beartype)} not beartype configuration.')

        # Initialize our superclass.
        super().__init__()

        # Classify all passed parameters.
        self._conf_beartype = conf_beartype

        # Nullify all remaining instance variables for safety.
        self._node_stack_beartype: List[AST] = []

    # ..................{ SUPERCLASS                         }..................
    # Overridden methods first defined by the "NodeTransformer" superclass.

    def generic_visit(self, node: NodeT) -> NodeT:
        '''
        Recursively visit and possibly transform *all* child nodes of the passed
        parent node in-place (i.e., preserving this parent node as is).

        Parameters
        ----------
        node : NodeT
            Parent node to transform *all* child nodes of.

        Returns
        ----------
        NodeT
            Parent node returned and thus preserved as is.
        '''

        # Add this parent node to the top of the stack of all current parent
        # nodes *BEFORE* visiting any child nodes of this parent node.
        self._node_stack_beartype.append(node)

        # Recursively visit *ALL* child nodes of this parent node.
        super().generic_visit(node)

        # Remove this parent node from the top of the stack of all current
        # parent nodes *AFTER* visiting all child nodes of this parent node.
        self._node_stack_beartype.pop()

        # Return this parent node as is.
        return node

    # ..................{ VISITORS ~ module                  }..................
    def visit_Module(self, node: Module) -> Module:
        '''
        Add a new abstract syntax tree (AST) child node to the passed
        **module node** (i.e., node encapsulating the module currently being
        loaded by the
        :class:`beartype.claw._importlib._clawimpload.BeartypeSourceFileLoader`)
        importing various attributes required by lower-level child nodes added
        by subsequent visitor methods defined by this transformer.

        Specifically, this method adds nodes importing:

        * Our private
          :func:`beartype._decor.decorcore.beartype_object_nonfatal` decorator.
        * Our private
          :obj:`beartype.claw._clawcache.claw_state` singleton global.
        * Our public :func:`beartype.door.die_if_unbearable` exception raiser.

        Parameters
        ----------
        node : Module
            Module node to be transformed.

        Returns
        ----------
        Module
            That same module node.
        '''

        # 0-based index of the first safe position in the list of all child
        # nodes of this parent module node to insert an import statement
        # importing our beartype decorator, initialized to the erroneous index
        # "-1" to enable detection of empty modules (i.e., modules whose module
        # nodes containing *NO* child nodes) below.
        node_import_beartype_attrs_index = -1

        # Child node of this parent module node immediately preceding the output
        # import child node to be added below, defaulting to this parent module
        # node to ensure that the copy_node_metadata() function below
        # *ALWAYS* copies from a valid node (for simplicity).
        node_import_prev: AST = node

        # For the 0-based index and value of each direct child node of this
        # parent module node...
        #
        # This iteration efficiently finds "node_import_beartype_attrs_index"
        # (i.e., the 0-based index of the first safe position in the list of all
        # child nodes of this parent module node to insert an import statement
        # importing our beartype decorator). Despite superficially appearing to
        # perform a linear search of all n child nodes of this module parent
        # node and thus exhibit worst-case O(n) time complexity, this iteration
        # is guaranteed to exhibit worst-case O(1) time complexity. \o/
        #
        # Note that the "body" instance variable for module nodes is a list of
        # all child nodes of this parent module node.
        for node_import_beartype_attrs_index, node_import_prev in enumerate(
            node.body):
            # If it is *NOT* the case that this child node signifies either...
            if not (
                # A module docstring...
                #
                # If that module defines a docstring, that docstring *MUST* be
                # the first expression of that module. That docstring *MUST* be
                # explicitly found and iterated past to ensure that the import
                # statement added below appears *AFTER* rather than *BEFORE* any
                # docstring. (The latter would destroy the semantics of that
                # docstring by reducing that docstring to an ignorable string.)
                (
                    isinstance(node_import_prev, Expr) and
                    isinstance(node_import_prev.value, Str)
                ) or
                # A future import (i.e., import of the form "from __future__
                # ...") *OR*...
                #
                # If that module performs one or more future imports, these
                # imports *MUST* necessarily be the first non-docstring
                # statement of that module and thus appear *BEFORE* all import
                # statements that are actually imports -- including the import
                # statement added below.
                (
                    isinstance(node_import_prev, ImportFrom) and
                    node_import_prev.module == '__future__'
                )
            # Then immediately halt iteration, guaranteeing O(1) runtime.
            ):
                break
            # Else, this child node signifies either a module docstring of
            # future import. In this case, implicitly skip past this child node
            # to the next child node.
            #
        # "node_import_beartype_attrs_index" is now the index of the first safe
        # position in this list to insert output child import nodes below.

        # If this is *NOT* the erroneous index to which this index was
        # initialized above, this module contains one or more child nodes and is
        # thus non-empty. In this case...
        if node_import_beartype_attrs_index >= 0:
            # Module-scoped import nodes (i.e., child nodes to be inserted under
            # the parent node encapsulating the currently visited submodule in
            # the AST for that module).
            #
            # Note that:
            # * The original attributes are imported into the currently visited
            #   submodule under obfuscated beartype-specific names,
            #   significantly reducing the likelihood of a namespace collision
            #   with existing attributes of the same name in that submodule.
            # * These nodes are intentionally *NOT* generalized into global
            #   constants. In theory, doing so would reduce space and time
            #   complexity by enabling efficient reuse here. In practice, doing
            #   so would also be fundamentally wrong; these nodes are
            #   subsequently modified to respect the source code metadata (e.g.,
            #   line numbers) of this AST module parent node, which prevents
            #   such trivial reuse. Although we could further attempt to
            #   circumvent that by shallowly or deeply copying from global
            #   constants, both the copy() and deepcopy() functions defined by
            #   the standard "copy" module are pure-Python and thus shockingly
            #   slow -- which defeats the purpose.

            # Node importing our private
            # beartype._decor.decorcore.beartype_object_nonfatal() decorator.
            node_import_decorator = make_node_importfrom(
                module_name=BEARTYPE_DECORATOR_MODULE_NAME,
                source_attr_name=BEARTYPE_DECORATOR_SOURCE_ATTR_NAME,
                target_attr_name=BEARTYPE_DECORATOR_TARGET_ATTR_NAME,
                node_sibling=node_import_prev,
            )

            # Node importing our public beartype.door.die_if_unbearable()
            # exception-raiser, intentionally imported from our private
            # "beartype.door._doorcheck" submodule rather than our public
            # "beartype.door" subpackage. Why? Because the former consumes
            # marginally less space and time to import than the latter. Whereas
            # the latter imports the full "TypeHint" hierarchy, the former only
            # imports low-level utility functions.
            node_import_raiser = make_node_importfrom(
                module_name=BEARTYPE_RAISER_MODULE_NAME,
                source_attr_name=BEARTYPE_RAISER_SOURCE_ATTR_NAME,
                target_attr_name=BEARTYPE_RAISER_TARGET_ATTR_NAME,
                node_sibling=node_import_prev,
            )

            # Node importing our private "claw_state" singleton.
            node_import_claw_state = make_node_importfrom(
                module_name=BEARTYPE_CLAW_STATE_MODULE_NAME,
                source_attr_name=BEARTYPE_CLAW_STATE_SOURCE_ATTR_NAME,
                target_attr_name=BEARTYPE_CLAW_STATE_TARGET_ATTR_NAME,
                node_sibling=node_import_prev,
            )

            # Insert these output child import nodes at this safe position of
            # the list of all child nodes of this parent module node.
            #
            # Note that this syntax efficiently (albeit unreadably) inserts
            # these output child import nodes at the desired index (in this
            # arbitrary order) of this parent module node.
            node.body[node_import_beartype_attrs_index:0] = (
                node_import_decorator,
                node_import_raiser,
                node_import_claw_state,
            )
        # Else, this module is empty. In this case, silently reduce to a noop.
        # Since this edge case is *EXTREMELY* uncommon, avoid optimizing for
        # this edge case (here or elsewhere).

        # Recursively transform *ALL* child nodes of this parent module node.
        node = self.generic_visit(node)

        # #FIXME: Conditionally perform this logic if "conf.is_debug", please.
        # print(
        #     f'Module abstract syntax tree (AST) transformed by @beartype to:\n\n'
        #     f'{get_node_repr_indented(node)}'
        # )

        # Return this transformed module node.
        return node

    # ..................{ VISITORS ~ class                   }..................
    #FIXME: Implement us up, please.
    def visit_ClassDef(self, node: ClassDef) -> Optional[ClassDef]:
        '''
        Add a new child node to the passed **class node** (i.e., node
        encapsulating the definition of a pure-Python class) unconditionally
        decorating that class by our private
        :func:`beartype._decor.decorcore.beartype_object_nonfatal` decorator.

        Parameters
        ----------
        node : ClassDef
            Class node to be transformed.

        Returns
        ----------
        Optional[ClassDef]
            This same class node.
        '''

        # Add a new child decoration node to this parent class node decorating
        # this class by @beartype under this configuration.
        decorate_node(node=node, conf=self._conf_beartype)

        # Recursively transform *ALL* child nodes of this parent class node.
        # Note that doing so implicitly calls the visit_FunctionDef() method
        # (defined below), each of which then effectively reduces to a noop.
        return self.generic_visit(node)

    # ..................{ VISITORS ~ callable                }..................
    def visit_FunctionDef(self, node: NodeCallable) -> Optional[NodeCallable]:
        '''
        Add a new child node to the passed **callable node** (i.e., node
        encapsulating the definition of a pure-Python function or method)
        decorating that callable by our private
        :func:`beartype._decor.decorcore.beartype_object_nonfatal` decorator if
        and only if that callable is **typed** (i.e., annotated by a return type
        hint and/or one or more parameter type hints).

        Parameters
        ----------
        node : NodeCallable
            Callable node to be transformed.

        Returns
        ----------
        Optional[NodeCallable]
            This same callable node.
        '''

        # If...
        if (
            # This callable node has one or more parent nodes previously visited
            # by this node transformer *AND*...
            self._node_stack_beartype and
            # The immediate parent node of this callable node is a class node...
            isinstance(self._node_stack_beartype[-1], ClassDef)
        ):
            # Then this callable node encapsulates a method rather than a
            # function. In this case, the visit_ClassDef() method defined above
            # has already explicitly decorated the class defining this method by
            # the @beartype decorator, which then implicitly decorates both this
            # and all other methods of that class by that decorator. For safety
            # and efficiency, avoid needlessly re-decorating this method by the
            # same decorator by simply preserving and returning this node as is.
            return node
        # Else, this callable node encapsulates a function rather than a method.
        # In this case, this function has yet to be decorated. Do so now, I say!

        # If the currently visited callable is annotated by one or more type
        # hints and thus *NOT* ignorable with respect to beartype decoration...
        if is_node_callable_typed(node):
            # Add a new child decoration node to this parent callable node
            # decorating this callable by @beartype under this configuration.
            decorate_node(node=node, conf=self._conf_beartype)
        # Else, that callable is ignorable. In this case, avoid needlessly
        # decorating that callable by @beartype for efficiency.

        # Recursively transform *ALL* child nodes of this parent callable node.
        return self.generic_visit(node)

    # ..................{ VISITORS ~ pep : 526               }..................
    def visit_AnnAssign(self, node: AnnAssign) -> NodeVisitResult:
        '''
        Add a new child node to the passed **annotated assignment node** (i.e.,
        node signifying the assignment of an attribute annotated by a
        :pep:`526`-compliant type hint) inserting a subsequent statement
        following that annotated assignment type-checking that attribute against
        that type hint by passing both to our :func:`beartype.door.is_bearable`
        tester.

        Note that the :class:`.AnnAssign` subclass defines these instance
        variables:

        * ``node.annotation``, a child node describing the PEP-compliant type
          hint annotating this assignment, typically an instance of either:

          * :class:`ast.Name`.
          * :class:`ast.Str`.

          Note that this node is *not* itself a valid PEP-compliant type hint
          and should *not* be treated as such here or elsewhere.
        * ``node.target``, a child node describing the target attribute assigned
          to by this assignment, guaranteed to be an instance of either:

          * :class:`ast.Name`, in which case this assignment is denoted as
            "simple" via the ``node.simple`` instance variable. This is the
            common case in which the attribute being assigned to is *NOT*
            embedded in parentheses and thus denotes a simple attribute name
            rather than a full-blown Python expression.
          * :class:`ast.Subscript`, in which case this assignment is to the item
            subscripted by an index of a container rather than to that container
            itself.
          * :class:`ast.Attribute`. **WE HAVE NO IDEA.** Look. We just don't.

        * ``node.simple``, an integer :superscript:`sigh` that is either:

          * If ``node.target`` is an :class:`ast.Name` node, 1.
          * Else, 0.

        * ``node.value``, an optional child node defined as either:

          * If this attribute is actually assigned to, a node encapsulating
            the new value assigned to this target attribute.
          * Else, :data:`None`.

          You may now be thinking to yourself as you wear a bear hat while
          rummaging through this filthy code: "What do you mean, 'if this
          attribute is actually assigned to'? Isn't this attribute necessarily
          assigned to? Isn't that what the 'AnnAssign' subclass means? I mean,
          it's right there in the bloody subclass name: 'AnnAssign', right?
          Clearly, *SOMETHING* is bloody well being assigned to. Right?"
          Wrong. The name of the :class:`.AnnAssign` subclass was poorly chosen.
          That subclass ambiguously encapsulates both:

          * Annotated variable assignments (e.g., ``muh_attr: int = 42``).
          * Annotated variables *without* assignments (e.g., ``muh_attr: int``).

        Parameters
        ----------
        node : AnnAssign
            Annotated assignment node to be transformed.

        Returns
        ----------
        NodeVisitResult
            Either:

            * If this annotated assignment node is *not* **simple** (i.e., the
              attribute being assigned to is embedded in parentheses and thus
              denotes a full-blown Python expression rather than a simple
              attribute name), that same parent node unmodified.
            * If this annotated assignment node is *not* **assigned** (i.e., the
              attribute in question is simply annotated with a type hint rather
              than both annotated with a type hint *and* assigned to), that same
              parent node unmodified.
            * Else, a 2-list comprising both that node and a new adjacent
              :class:`Call` node performing this type-check.

        See Also
        ----------
        https://github.com/awf/awfutils
            Third-party Python package whose ``@awfutils.typecheck`` decorator
            implements statement-level :func:`isinstance`-based type-checking in
            a similar manner, strongly inspiring this implementation. Thanks so
            much to Cambridge researcher @awf (Andrew Fitzgibbon) for the
            phenomenal inspiration!
        '''

        # If either...
        if (
            # This beartype configuration disables type-checking of PEP
            # 526-compliant annotated variable assignments *OR*...
            not self._conf_beartype.claw_is_pep526 or
            # This beartype configuration enables type-checking of PEP
            # 526-compliant annotated variable assignments *BUT*...

            #FIXME: Can and/or should we also support "node.target" child nodes
            #that are instances of "ast.Attribute" and "ast.Subscript"?
            # This assignment is *NOT* simple (in which case this assignment is
            # *NOT* assigning to an attribute name) *OR*...
            not node.simple or
            # This assignment is simple *BUT*...
            #
            # This assignment is *NOT* actually an assignment but simply an
            # unassigned annotation of an attribute...
            not node.value
        ):
            # Then silently ignore this assignment.
            return node
        # Else:
        # * This beartype configuration enables type-checking of PEP
        #   526-compliant annotated variable assignments.
        # * This assignment is simple and assigning to an attribute name.

        # Validate this expectation.
        assert isinstance(node.target, Name), (
            f'Non-simple AST annotated assignment node {repr(node)} '
            f'target {repr(node.target)} not {repr(Name)} instance.')

        # Child node referencing the function performing this type-checking,
        # previously imported at module scope by visit_FunctionDef() above.
        node_func_name = Name(
            BEARTYPE_RAISER_TARGET_ATTR_NAME, ctx=NODE_CONTEXT_LOAD)

        # Child node passing the value newly assigned to this attribute by this
        # assignment as the first parameter to die_if_unbearable().
        node_func_arg_pith = Name(node.target.id, ctx=NODE_CONTEXT_LOAD)

        # List of all nodes encapsulating keyword arguments passed to
        # die_if_unbearable(), defaulting to the empty list and thus *NO* such
        # keyword arguments.
        node_func_kwargs = []

        # If the current beartype configuration is *NOT* the default beartype
        # configuration, this configuration is a user-defined beartype
        # configuration which *MUST* be passed as well. In this case...
        if self._conf_beartype != BEARTYPE_CONF_DEFAULT:
            # Node encapsulating the passing of this configuration as
            # the "conf" keyword argument to die_if_unbearable().
            node_func_kwarg_conf = make_node_keyword_conf(node_sibling=node)

            # Append this node to the list of all keyword arguments passed to
            # die_if_unbearable().
            node_func_kwargs.append(node_func_kwarg_conf)
        # Else, this configuration is simply the default beartype
        # configuration. In this case, avoid passing that configuration to
        # the beartype decorator for both efficiency and simplicity.

        # Child node type-checking this newly assigned attribute against the
        # type hint annotating this assignment via our die_if_unbearable().
        node_func_call = Call(
            func=node_func_name,
            args=[
                # Child node passing the value newly assigned to this
                # attribute by this assignment as the first parameter.
                node_func_arg_pith,
                # Child node passing the type hint annotating this assignment as
                # the second parameter.
                node.annotation,
            ],
            keywords=node_func_kwargs,
        )

        # Adjacent node encapsulating this type-check as a Python statement.
        node_func = Expr(node_func_call)

        # Copy all source code metadata from this AST annotated assignment node
        # onto *ALL* AST nodes created above.
        copy_node_metadata(node_src=node, node_trg=(
            node_func_name,
            node_func_arg_pith,
            node_func_call,
            node_func,
        ))

        # Return a list comprising these two adjacent nodes.
        #
        # Note that order is *EXTREMELY* significant. This order ensures that
        # this attribute is type-checked after being assigned to, as expected.
        return [node, node_func]
