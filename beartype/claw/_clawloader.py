#!/usr/bin/env python3
# --------------------( LICENSE                            )--------------------
# Copyright (c) 2014-2022 Beartype authors.
# See "LICENSE" for further details.

'''
**Beartype all-at-once low-level** :mod:`importlib` **module loading
machinery.**

This private submodule declares an :mod:`importlib`-compatible module loader
dynamically decorating all typed callables and classes of all submodules of all
packages previously registered with our global package name cache (e.g., by a
prior call to the high-level public :func:`beartype.claw.beartype_all` or
:func:`beartype.claw.beartype_package` functions) with the
:func:`beartype.beartype` decorator with the abstract syntax tree (AST)
transformation defined by the low-level :mod:`beartype.claw._clawast` submodule.

This private submodule is *not* intended for importation by downstream callers.
'''

# ....................{ IMPORTS                            }....................
from ast import (
    PyCF_ONLY_AST,
    # fix_missing_locations,
)
from beartype.claw._clawast import _BeartypeNodeTransformer
# from beartype.claw._clawpackagenames import (
#     is_packages_registered,
#     register_packages,
# )
# from beartype.roar import BeartypeClawRegistrationException
from beartype.typing import (
    Optional,
)
# from beartype._conf import BeartypeConf
from importlib import (  # type: ignore[attr-defined]
    _bootstrap_external,
)
from importlib.machinery import (
    SourceFileLoader,
)
from importlib.util import (
    decode_source,
)
from types import (
    CodeType,
)

# Original cache_from_source() function defined by the private (*gulp*)
# "importlib._bootstrap_external" submodule, preserved *BEFORE* temporarily
# replacing that function with our beartype-specific variant below.
from importlib.util import cache_from_source as cache_from_source_original

# See the "beartype.cave" submodule for further commentary.
__all__ = ['STAR_IMPORTS_CONSIDERED_HARMFUL']

# ....................{ CLASSES                            }....................
#FIXME: Generalize this class to support stacking. What? Okay, so the core
#issue with the prior approach is that it only works with standard Python
#modules defined as standard files in standard directories. This assumption
#breaks down for Python modules embedded within other files (e.g., as frozen
#archives or zip files). The key insight here is given by Iguananaut in this
#StackOverflow answer:
#  https://stackoverflow.com/a/48671982/2809027
#This approach "...installs a special hook in sys.path_hooks that acts almost
#as a sort of middle-ware between the PathFinder in sys.meta_path, and the
#hooks in sys.path_hooks where, rather than just using the first hook that
#says 'I can handle this path!' it tries all matching hooks in order, until it
#finds one that actually returns a useful ModuleSpec from its find_spec
#method."
#Note that "hooks" in "sys.path_hooks" are actually *FACTORY FUNCTIONS*,
#typically defined by calling the FileFinder.path_hook() class method.
#We're unclear whether we want a full "ModuleSpec," however. It seems
#preferable to merely search for a working hook in "sys.path_hooks" that
#applies to the path. Additionally, if that hook defines a get_source() method
#*AND* that method returns a non-empty string (i.e., that is neither "None"
#*NOR* the empty string), then we want to munge that string with our AST
#transformation. The advantages of this approach are multitude:
#* This approach supports pytest, unlike standard "meta_path" approaches.
#* This approach supports embedded files, unlike the first approach above. In
#  particular, note that the standard
#  "zipimporter.zipimporter(_bootstrap_external._LoaderBasics)" class for
#  loading Python modules from arbitrary zip files does *NOT* subclass any of
#  the standard superclasses you might expect it to (e.g.,
#  "importlib.machinery.SourceFileLoader"). Ergo, a simple inheritance check
#  fails to suffice. Thankfully, that class *DOES* define a get_source()
#  method resembling that of SourceFileLoader.get_source().
#FIXME: I've confirmed by deep inspection of both the standard "importlib"
#package and the third-party "_pytest.assertion.rewrite" subpackage that the
#above should (but possible does *NOT*) suffice to properly integrate with
#pytest. Notably, the
#_pytest.assertion.rewrite.AssertionRewritingHook.find_spec() class method
#improperly overwrites the "importlib._bootstrap.ModuleSpec.loader" instance
#variable with *ITSELF* here:
#
#    class AssertionRewritingHook(importlib.abc.MetaPathFinder, importlib.abc.Loader):
#        ...
#
#        _find_spec = importlib.machinery.PathFinder.find_spec
#
#        def find_spec(
#            self,
#            name: str,
#            path: Optional[Sequence[Union[str, bytes]]] = None,
#            target: Optional[types.ModuleType] = None,
#        ) -> Optional[importlib.machinery.ModuleSpec]:
#            ...
#
#            # *SO FAR, SO GOOD.* The "spec.loader" instance variable now refers
#            # to an instance of our custom "SourceFileLoader" subclass.
#            spec = self._find_spec(name, path)  # type: ignore
#            ...
#
#            # *EVEN BETTER.* This might save us. See below.
#            if not self._should_rewrite(name, fn, state):
#                return None
#
#            # And... everything goes to Heck right here. Passing "loader=self"
#            # completely replaces the loader that Python just helpfully
#            # provided with this "AssertionRewritingHook" instance, which is
#            # all manner of wrong.
#            return importlib.util.spec_from_file_location(
#                name,
#                fn,
#                loader=self,  # <-- *THIS IS THE PROBLEM, BRO.*
#                submodule_search_locations=spec.submodule_search_locations,
#            )
#
#Ultimately, it's no surprise whatsoever that this brute-force behaviour from
#pytest conflicts with everyone else in the Python ecosystem. That said, this
#might still not be an issue. Why? Because the call to self._should_rewrite()
#*SHOULD* cause "AssertionRewritingHook" to silently reduce to a noop for
#anything that beartype would care about.
#
#If true (which it should be), the above approach *SHOULD* still actually work.
#So why does pytest conflict with other AST transformation approaches? Because
#those other approaches also leverage "sys.meta_path" machinery, typically by
#forcefully prepending their own "MetaPathFinder" instance onto "sys.meta_path",
#which silently overwrites pytest's "MetaPathFinder" instance. Since we're *NOT*
#doing that, we should be fine with our approach. *sweat beads brow*

#FIXME: Unit test us up, please.
class BeartypeSourceFileLoader(SourceFileLoader):
    '''
    **Beartype source file loader** implementing :mod:`importlib` machinery
    loading a **sourceful Python package or module** (i.e., package or module
    backed by a ``.py``-suffixed source file) into a **module spec** (i.e.,
    in-memory :class:`importlib._bootstrap.ModuleSpec` instance describing the
    importation of that package or module, complete with a reference back to
    this originating loader).

    The :func:`beartype_package` function injects a low-level **import path
    hook** (i.e., factory closure instantiating this class as an item of the
    standard :mod:`sys.path_hooks` list) to the front of that list. When called
    by a higher-level parent **import metapath hook** (i.e., object suitable for
    use as an item of the standard :mod:`sys.meta_path` list), that closure:

    #. Instantiates one instance of the standard
       :class:`importlib._bootstrap_external.FileFinder` class for each
       **imported Python package** (i.e., package on the :mod:`sys.path` list).
       The :meth:``importlib._bootstrap_external.FileFinder.find_spec` method of
       that instance then returns this :class:`BeartypeSourceFileLoader` class
       uninstantiated for each **imported Python package submodule** (i.e.,
       submodule directly contained in that package).
    #. Adds a new key-value pair to the standard :mod:`sys.path_importer_cache`
       dictionary, whose:

       * Key is the package of that module.
       * Value is that instance of this class.

    Motivation
    ----------
    This loader was intentionally implemented so as to exclusively leverage the
    lower-level :attr:`sys.path_hooks` mechanism for declaring import hooks
    rather than both that *and* the higher-level :attr:`sys.meta_path`
    mechanism. All prior efforts in the Python ecosystem to transform the
    abstract syntax trees (ASTs) of modules at importation time via import hooks
    leverage both mechanisms. This includes:

    * :mod:`pytest`, which rewrites test assertion statements via import hooks
      leveraging both mechanisms.
    * :mod:`typeguard`, which implicitly applies the runtime type-checking
      :func:`typguard.typechecked` decorator via import hooks leveraging both
      mechanisms.
    * :mod:`ideas`, which applies arbitrary caller-defined AST transformations
      via (...wait for it) import hooks leveraging both mechanisms.

    Beartype subverts this long-storied tradition by *only* leveraging the
    lower-level :attr:`sys.path_hooks` mechanism. Doing so reduces the
    maintenance burden, code complexity, and inter-package conflicts. The latter
    point is particularly salient. The AST transformations applied by
    both :mod:`typeguard` and :mod:`ideas` accidentally conflict with those
    applied by :mod:`pytest`. Why? Because (in order):

    #. When run as a test suite runner, :mod:`pytest` necessarily runs first and
       thus prepends its import hook as the new first item of the
       :attr:`sys.meta_path` list.
    #. When imported during subsequent unit and/or integration testing under
       that test suite runner, :mod:`typeguard` and :mod:`ideas` then install
       their own import hooks as the new first item of the :attr:`sys.meta_path`
       list. The import hook previously prepended by :mod:`pytest` then becomes
       the second item  of the :attr:`sys.meta_path` list. Python consults both
       the :attr:`sys.meta_path` and :attr:`sys.path_hooks` lists in a
       first-come-first-served manner. The first hook on each list satisfying a
       request to find and import a module being imported terminates that
       request; no subsequent hooks are consulted. Both :mod:`typeguard` and
       :mod:`ideas` fail to iteratively consult subsequent hooks (e.g., with a
       piggybacking scheme of some sort). Both squelch the hook previously
       installed by :mod:`pytest` that rewrote assertions. That is bad.

    Parameters
    ----------
    _module_being_loaded_name : Optional[str]
        Either:

        * If the :meth:`get_code` method has been externally called to load a
          module (i.e., create and return the code object underlying that
          module), the fully-qualified name of that module. This instance
          variable enables our override of the parent :meth:`get_code` method to
          communicate this name to the child :meth:`source_to_code` method,
          which fails to accept and thus otherwise has *no* access to this name.
          The superclass implementation of the :meth:`get_code` method
          internally calls our override of the :meth:`source_to_code` method.
        * Else, ``None``.

    See Also
    ----------
    * The `comparable "typeguard.importhook" submodule <typeguard import
      hook_>`__ implemented by the incomparable `@agronholm (Alex Grönholm)
      <agronholm_>`__, whose intrepid solutions strongly inspired this
      subpackage. `Typeguard's import hook infrastructure <typeguard import
      hook_>`__ is a significant improvement over the prior state of the art in
      Python and a genuine marvel of concise, elegant, and portable abstract
      syntax tree (AST) transformation.

    .. _agronholm:
       https://github.com/agronholm
    .. _typeguard import hook:
       https://github.com/agronholm/typeguard/blob/master/src/typeguard/importhook.py
    '''

    # ..................{ INITIALIZERS                       }..................
    def __init__(self, *args, **kwargs) -> None:
        '''
        Initialize this beartype source file loader.

        All passed parameters are passed as is to the superclass method, which
        then calls our lower-level :meth:`source_to_code` subclass method
        overridden below.
        '''

        # Initialize our superclass with all passed parameters.
        super().__init__(*args, **kwargs)

        # Nullify all subclass-specific instance variables for safety.
        self._module_being_loaded_name: Optional[str] = None

    # ..................{ FINDER API                         }..................
    # The importlib._bootstrap_external.*Finder API declares the high-level
    # find_spec() method, which creates and returns either an
    # "importlib._bootstrap.ModuleSpec" instance if this finder can load the
    # passed package or module *OR* "None" otherwise, in which case the next
    # finder on "sys.path_hooks" is consulted.

    #FIXME: We also need to also:
    #* Define the find_spec() method, which should:
    #  * Efficiently test whether the passed "path" is in "_package_names" in a
    #    *THREAD-SAFE MANNER.*
    #  * If not, this method should reduce to a noop by returning "None".
    #  * Else, this method should return the value of calling the superclass
    #    find_spec() implementation.
    #  We're fairly certain that suffices. Nonetheless, verify this by
    #  inspecting the comparable find_spec() implementation at:
    #      https://stackoverflow.com/a/48671982/2809027
    #FIXME: *PROBABLY INSUFFICIENT.* For safety, we really only want to apply
    #this loader to packages in the passed "package_names" list. For all other
    #packages, the relevant method of this loader (which is probably
    #find_spec(), but let's research that further) should return "None". Doing
    #so defers loading to the next loader in "sys.path_hooks".
    #FIXME: *AH-HA.* We basically have *NO* means of overriding Finder API
    #behaviour here, for the obvious reason that the pivotal "FileFinder" class
    #resides outside the scope of the lower-level "SourceFileLoader" superclass.
    #Instead:
    #* Remove this entire "FINDER API" section.
    #* Fold the desired conditionality into the get_code() and source_to_code()
    #  methods overridden below. Doing so is mildly complicated by the fact that
    #  the fully-qualified name of the module to be loaded is passed *ONLY* to
    #  the parent get_code() method; the child source_to_code() method called by
    #  the parent get_code() method is *NOT* passed that name. Ordinarily, this
    #  would scuttle everything. In this case, however, the *ONLY* method
    #  calling source_to_code() anywhere throughout the "importlib" package is
    #  get_code(). Ergo, source_to_code() should have been made private (i.e.,
    #  as _source_to_code()). Of course, that's irrelevant. What's relevant is
    #  that we can actually do this as follows:
    #  * In get_code():
    #    1. Cache the passed "fullname" as a new
    #       "self._module_being_loaded_name" instance variable (or something).
    #    2. Call super().get_code().
    #    3. Nullify that same instance variable for safety.
    #  * In source_to_code():
    #    1. If that same instance variable is "None", raise an exception.
    #    2. Else, use that variable to decide whether or not the current module
    #       being loaded should be decorated by @beartype. If so, apply our AST
    #       transformation; else, don't. Simple. Let's do this, fam.
    #FIXME: *AH-HA*! The prior comment would absolutely work -- but we can do
    #something even marginally better. Why? Because source_to_code() only needs
    #to know whether or not it should @beartype the passed module; it doesn't
    #actually need the name of that module. Ergo, we should:
    #* Define a new _clawpackagenames.is_package_registered() tester with
    #  signature resembling:
    #      def is_package_registered(package_name: str) -> bool:
    #  As the signature suggests, that tester returns true only if the package
    #  with the passed name has been previously registered for beartyping. This
    #  method's implementation will be slightly non-trivial, as it will recurse
    #  over our global package name cache.
    #* Rename the "_module_being_loaded_name" instance variable to:
    #      self._is_module_beartypeable: Optional[bool] = None
    #* In get_code():
    #  * Split the parent package name from the passed module "fullname": e.g.,
    #      package_name = fullname.rpartition('.')[0]
    #  * Set "_is_module_beartypeable" as follows:
    #      self._is_module_beartypeable = is_package_registered(package_name)
    #  * If "_is_module_beartypeable" is True, call super().get_code() as we
    #    currently do (i.e., embedded in an inefficient try-finally block).
    #  * Else, call super().get_code() *WITHOUT* inefficiently monkey-patching
    #    cache_from_source(). This isn't simply an optimization, however. This
    #    is critical. Why? Because modules *NOT* being @beartyped should be
    #    compiled to their standard ".pyc" files.
    #  * In either case, Nullify "_is_module_beartypeable" for safety.
    #* In source_to_code():
    #  * If "_is_module_beartypeable" is "None", raise an exception.
    #  * Else, use that variable to decide whether or not the current module
    #    being loaded should be decorated by @beartype. If so, apply our AST
    #    transformation; else, don't. Simple. Let's do this, fam.

    # ..................{ LOADER API                         }..................
    # The importlib._bootstrap_external.*Loader API declares the low-level
    # exec_module() method, which accepts a "importlib._bootstrap.ModuleSpec"
    # instance created and returned by a prior call to the higher-level
    # find_spec() method documented above; the exec_module() method then uses
    # that module spec to create and return a fully imported module object
    # (i.e., "types.ModuleType" instance). To do so:
    # * The default exec_module() implementation internally calls the
    #   lower-level get_code() method returning an in-memory Python code object
    #   deserialized from the on-disk or in-memory bytes underlying that module.
    # * The default get_code() implementation internally calls the
    #   lower-level source_to_code() method returning an in-memory Python code
    #   object dynamically compiled from the passed in-memory bytes.

    #FIXME: Under the "Motivation" section, additionally document why we're
    #actually applying this monkey-patch. The reason is that a @beartyped module
    #should *NEVER* be compiled to the same "__pycache__/*.pyc" file that the
    #non-@beartyped variant of the same module is compiled to. Clearly, doing so
    #would invite a conflict. Since @beartype-ing can be trivially enabled and
    #disabled for any given module by either calling or not calling the public
    #beartype.claw.beartype_package() function with that module's parent
    #package, compiling a @beartyped module to the same "__pycache__/*.pyc" file
    #as the non-@beartyped variant of the same module would erroneously persist
    #@beartyping to that module -- even after removing the call to the
    #beartype.claw.beartype_package() function passed that module's parent
    #package! Clearly, that's awful. That's what @agronholm's ingenious patch is
    #all about. Wow. We're exceptionally impressed, as always.
    def get_code(self, *args, **kwargs) -> Optional[CodeType]:
        '''
        Create and return the code object underlying the passed module.

        All passed parameters are passed as is to the superclass method, which
        then calls our lower-level :meth:`source_to_code` subclass method
        overridden below.

        Motivation
        ----------
        This override of the :meth:`get_code` method temporarily replaces the
        private :func:`importlib._bootstrap_external.cache_from_source` function
        with a beartype-specific variant. Doing so is strongly inspired
        by a similar monkey-patch applied by the external
        :meth:`typeguard.importhook.TypeguardLoader.exec_module` method authored
        by the incomparable @agronholm (Alex Grönholm), who writes:

            Use a custom optimization marker – the import lock should make
            this monkey patch safe

        We implicitly trust @agronholm to get that right in a popular project
        stress-tested across hundreds of open-source projects over the past
        several decades. So, we avoid explicit thread-safe locking here.

        Lastly, note there appears to be *no* other means of safely
        implementing this behaviour *without* violating Don't Repeat Yourself
        (DRY). Specifically, doing so would require duplicating most of the
        entirety of the *extremely* non-trivial nearly 100 line-long
        :meth:`importlib._bootstrap_external.SourceLoader.get_code` method.
        Since duplicating non-trivial and fragile code inherently tied to a
        specific CPython version is considerably worse than applying a trivial
        one-line monkey-patch, first typeguard and now @beartype strongly prefer
        this monkey-patch. Did we mention that @agronholm is amazing? Because
        that really bears repeating. May the name of Alex Grönholm live eternal!
        '''

        # Temporarily replace that function with a beartype-specific variant.
        # Doing so is strongly inspired by a similar monkey-patch applied by the
        # external typeguard.importhook.TypeguardLoader.exec_module() method
        # authored by the incomparable @agronholm (Alex Grönholm), who writes:
        #     Use a custom optimization marker – the import lock should make
        #     this monkey patch safe
        #
        # We implicitly trust @agronholm to get that right in a popular project
        # stress-tested across hundreds of open-source projects over the past
        # several decades. So, we avoid explicit thread-safe locking here.
        #
        # Lastly, note there appears to be *NO* other means of safely
        # implementing this behaviour *WITHOUT* violating Don't Repeat Yourself
        # (DRY). Specifically, doing so would require duplicating most of the
        # entirety of the *EXTREMELY* non-trivial nearly 100 line-long
        # importlib._bootstrap_external.SourceLoader.get_code() method. Since
        # duplicating non-trivial and fragile code inherently tied to a specific
        # CPython version is considerably worse than applying a trivial one-line
        # monkey-patch, first typeguard and now @beartype strongly prefer this
        # monkey-patch. Did we mention that @agronholm is amazing? Because that
        # really bears repeating. May the name of Alex Grönholm live eternal!
        _bootstrap_external.cache_from_source = _cache_from_source_beartype

        # Attempt to defer to the superclass method with all passed parameters.
        try:
            return super().get_code(*args, **kwargs)
        # After doing so (and regardless of whether doing so raises an
        # exception), restore the original cache_from_source() function.
        finally:
            _bootstrap_external.cache_from_source = cache_from_source_original


    # Note that we explicitly ignore mypy override complaints here. For unknown
    # reasons, mypy believes that "importlib.machinery.SourceFileLoader"
    # subclasses comply with the "importlib.abc.InspectLoader" abstract base
    # class (ABC). Naturally, that is *NOT* the case. Ergo, we entirely ignore
    # mypy complaints here with respect to signature matching.
    def source_to_code(  # type: ignore[override]
        self,

        # Mandatory parameters.
        data: bytes,
        path: str,

        # Optional keyword-only parameters.
        *,
        _optimize: int =-1,
    ) -> CodeType:
        '''
        Code object dynamically compiled from the **sourceful Python package or
        module** (i.e., package or module backed by a ``.py``-suffixed source
        file) with the passed undecoded contents and filename, efficiently
        transformed in-place by our abstract syntax tree (AST) transformation
        automatically applying the :func:`beartype.beartype` decorator to all
        applicable objects of that package or module.

        The higher-level :meth:`get_code` superclass method internally calls
        this lower-level subclass method.

        Parameters
        ----------
        data : bytes
            **Byte array** (i.e., undecoded list of bytes) of the Python package
            or module to be decoded and dynamically compiled into a code object.
        path : str
            Absolute or relative filename of that Python package or module.
        _optimize : int, optional
            **Optimization level** (i.e., numeric integer signifying increasing
            levels of optimization under which to compile that Python package or
            module). Defaults to -1, implying the current interpreter-wide
            optimization level with which the active Python process was
            initially invoked (e.g., via the ``-o`` command-line option).

        Returns
        ----------
        CodeType
            Code object dynamically compiled from that Python package or module.
        '''

        # Plaintext decoded contents of that package or module.
        module_source = decode_source(data)

        # Abstract syntax tree (AST) dynamically parsed from these contents.
        module_ast = compile(
            module_source,
            path,
            'exec',
            PyCF_ONLY_AST,
            # Prevent these contents from inheriting the effects of any
            # "from __future__ import" statements in effect in beartype itself.
            dont_inherit=True,
            optimize=_optimize,
        )

        # Abstract syntax tree (AST) modified by our AST transformation
        # dynamically parsed from these contents.
        module_ast_beartyped = _BeartypeNodeTransformer().visit(module_ast)

        #FIXME: *THIS IS BAD, BRO.* For one thing, this is slow. Recursion is
        #slow. It's also dangerous. We shouldn't do it more than we have to. Now
        #we're recursing over the entire AST tree twice: once in our AST
        #transformation above (which is unavoidable) and again in the call to
        #fix_missing_locations() here (which probably is avoidable). Instead, we
        #should fold the logic of fix_missing_locations() directly into our AST
        #transformation. Specifically, our AST transformation should:
        #* For each newly generated AST node, non-recursively propagate the line
        #  and column numbers of that node's parent node onto that node.
        #FIXME: Fascinating. It would seem that we need to propagate these
        #*FOUR* attributes: "lineno", "end_lineno", "col_offset", and
        #"end_col_offset". Note that the "end_"-prefixed attributes may only be
        #available under newer Python versions. Say, Python ≥ 3.9 or 3.10?
        #Further research is required, clearly. Fortunately, the implementation
        #of the ast.fix_missing_locations() function is trivial. It shouldn't be
        #terribly arduous to embed equivalent functionality in our AST
        #transformation, assuming we think we know what we're doing. (We don't.)

        # Recursively propagate the line and column numbers of all parent nodes
        # in this tree to their children that failed to define these numbers
        # *BEFORE* calling the compile() builtin, which requires these numbers
        # to be defined on all AST nodes supporting these numbers. All AST nodes
        # supporting these numbers define these numbers *EXCEPT* those AST nodes
        # programmatically generated by our AST transformation, which currently
        # leaves these numbers undefined due to programmer laziness.
        #fix_missing_locations(module_ast_beartyped)

        # Code object dynamically compiled from that transformed AST.
        module_codeobj = compile(
            module_ast_beartyped,
            path,
            'exec',
            # Prevent these contents from inheriting the effects of any
            # "from __future__ import" statements in effect in beartype itself.
            dont_inherit=True,
            optimize=_optimize,
        )

        # Return this code object.
        return module_codeobj

# ....................{ PRIVATE ~ cachers                  }....................
#FIXME: Unit test us up, please.
def _cache_from_source_beartype(*args, **kwargs) -> str:
    '''
    Beartype-specific variant of the
    :func:`importlib._bootstrap_external.cache_from_source` function applying a
    beartype-specific optimization marker to that function.

    This, in turn, ensures that submodules residing in packages registered by a
    prior call to the :func:`beartype_submodules_on_import` function are
    compiled to files with the filetype ``".pyc{optimization}-beartype"``,
    where ``{optimization}`` is the original ``optimization`` parameter passed
    to this function call.
    '''

    # Original optimization parameter passed to this function call if any *OR*
    # the empty string otherwise.
    optimization_marker_old = kwargs.get('optimization', '')

    # New optimization parameter applied by this monkey-patch of that function,
    # uniquifying that parameter with a beartype-specific suffix.
    kwargs['optimization'] = f'{optimization_marker_old}-beartype'

    # Defer to the implementation of the original cache_from_source() function.
    return cache_from_source_original(*args, **kwargs)
