"""
Executes a set of implementations as a program.
"""

# Copyright (C) 2009, Thomas Leonard
# See the README file for details, or visit http://0install.net.

from zeroinstall import _
import os, sys
from logging import debug, info

from zeroinstall.injector.model import SafeException, EnvironmentBinding
from zeroinstall.injector.iface_cache import iface_cache

def do_env_binding(binding, path):
	"""Update this process's environment by applying the binding.
	@param binding: the binding to apply
	@type binding: L{model.EnvironmentBinding}
	@param path: the selected implementation
	@type path: str"""
	os.environ[binding.name] = binding.get_value(path,
					os.environ.get(binding.name, None))
	info("%s=%s", binding.name, os.environ[binding.name])

def execute(policy, prog_args, dry_run = False, main = None, wrapper = None):
	"""Execute program. On success, doesn't return. On failure, raises an Exception.
	Returns normally only for a successful dry run.
	@param policy: a policy with the selected versions
	@type policy: L{policy.Policy}
	@param prog_args: arguments to pass to the program
	@type prog_args: [str]
	@param dry_run: if True, just print a message about what would have happened
	@type dry_run: bool
	@param main: the name of the binary to run, or None to use the default
	@type main: str
	@param wrapper: a command to use to actually run the binary, or None to run the binary directly
	@type wrapper: str
	@precondition: C{policy.ready and policy.get_uncached_implementations() == []}
	"""
	execute_selections(policy.solver.selections, prog_args, dry_run, main, wrapper)

def _do_bindings(impl, bindings):
	for b in bindings:
		if isinstance(b, EnvironmentBinding):
			do_env_binding(b, _get_implementation_path(impl))

def _get_implementation_path(impl):
	return impl.local_path or iface_cache.stores.lookup_any(impl.digests)

def test_selections(selections, prog_args, dry_run, main, wrapper = None):
	"""Run the program in a child process, collecting stdout and stderr.
	@return: the output produced by the process
	@since: 0.27
	"""
	args = []
	import tempfile
	output = tempfile.TemporaryFile(prefix = '0launch-test')
	try:
		child = os.fork()
		if child == 0:
			# We are the child
			try:
				try:
					os.dup2(output.fileno(), 1)
					os.dup2(output.fileno(), 2)
					execute_selections(selections, prog_args, dry_run, main)
				except:
					import traceback
					traceback.print_exc()
			finally:
				sys.stdout.flush()
				sys.stderr.flush()
				os._exit(1)

		info(_("Waiting for test process to finish..."))

		pid, status = os.waitpid(child, 0)
		assert pid == child

		output.seek(0)
		results = output.read()
		if status != 0:
			results += _("Error from child process: exit code = %d") % status
	finally:
		output.close()
	
	return results

def execute_selections(selections, prog_args, dry_run = False, main = None, wrapper = None):
	"""Execute program. On success, doesn't return. On failure, raises an Exception.
	Returns normally only for a successful dry run.
	@param selections: the selected versions
	@type selections: L{selections.Selections}
	@param prog_args: arguments to pass to the program
	@type prog_args: [str]
	@param dry_run: if True, just print a message about what would have happened
	@type dry_run: bool
	@param main: the name of the binary to run, or None to use the default
	@type main: str
	@param wrapper: a command to use to actually run the binary, or None to run the binary directly
	@type wrapper: str
	@since: 0.27
	@precondition: All implementations are in the cache.
	"""
	sels = selections.selections
	for selection in sels.values():
		_do_bindings(selection, selection.bindings)
		for dep in selection.dependencies:
			dep_impl = sels[dep.interface]
			if not dep_impl.id.startswith('package:'):
				_do_bindings(dep_impl, dep.bindings)

	root_sel = sels[selections.interface]

	assert root_sel is not None

	command = selections.command

	# Process command's dependencies' bindings
	for dep in command.requires:
		dep_impl = sels[dep.interface]
		if not dep_impl.id.startswith('package:'):
			_do_bindings(dep_impl, dep.bindings)

	command_path = command.path

	if root_sel.id.startswith('package:'):
		main = main or command_path
		prog_path = main
	else:
		if command_path and command_path.startswith('/'):
			raise SafeException(_("Command path must be relative, but '%s' starts with '/'!") %
						command_path)
		if main is None:
			main = command_path		# User didn't specify a main path
		elif main.startswith('/'):
			main = main[1:]			# User specified a path relative to the package root
		elif command_path:
			main = os.path.join(os.path.dirname(command_path), main)	# User main is relative to command's name

		if main is not None:
			prog_path = os.path.join(_get_implementation_path(root_sel), main)

	if main is None:
		# This shouldn't happen, because the solve would fail first.
		raise Exception(_("Implementation '%s' cannot be executed directly; it is just a library "
				    "to be used by other programs (or missing 'main' attribute)") %
				    root_sel)

	if not os.path.exists(prog_path):
		raise SafeException(_("File '%(program_path)s' does not exist.\n"
				"(implementation '%(implementation_id)s' + program '%(main)s')") %
				{'program_path': prog_path, 'implementation_id': root_sel.id,
				'main': main})
	if wrapper:
		prog_args = ['-c', wrapper + ' "$@"', '-', prog_path] + list(prog_args)
		prog_path = '/bin/sh'

	if dry_run:
		print _("Would execute: %s") % ' '.join([prog_path] + prog_args)
	else:
		info(_("Executing: %s"), prog_path)
		sys.stdout.flush()
		sys.stderr.flush()
		try:
			os.execl(prog_path, prog_path, *prog_args)
		except OSError, ex:
			raise SafeException(_("Failed to run '%(program_path)s': %(exception)s") % {'program_path': prog_path, 'exception': str(ex)})
