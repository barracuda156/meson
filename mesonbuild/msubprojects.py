import os, subprocess
import argparse

from . import mlog
from .mesonlib import git, Popen_safe
from .wrap.wrap import API_ROOT, Resolver, WrapException
from .wrap import wraptool

def update_wrapdb_file(wrap, repo_dir, options):
    patch_url = wrap.get('patch_url')
    branch, revision = wraptool.parse_patch_url(patch_url)
    new_branch, new_revision = wraptool.get_latest_version(wrap.name)
    if new_branch == branch and new_revision == revision:
        mlog.log('  -> Up to date.')
        return True
    wraptool.update_wrap_file(wrap.filename, wrap.name, new_branch, new_revision)
    msg = ['  -> New wrap file downloaded.']
    # Meson reconfigure won't use the new wrap file as long as the source
    # directory exists. We don't delete it ourself to avoid data loss in case
    # user has changes in their copy.
    if os.path.isdir(repo_dir):
        msg += ['To use it, delete', mlog.bold(repo_dir), 'and run', mlog.bold('meson --reconfigure')]
    mlog.log(*msg)
    return True

def update_file(wrap, repo_dir, options):
    patch_url = wrap.values.get('patch_url', '')
    if patch_url.startswith(API_ROOT):
        return update_wrapdb_file(wrap, repo_dir, options)
    elif not os.path.isdir(repo_dir):
        # The subproject is not needed, or it is a tarball extracted in
        # 'libfoo-1.0' directory and the version has been bumped and the new
        # directory is 'libfoo-2.0'. In that case forcing a meson
        # reconfigure will download and use the new tarball.
        mlog.log('  -> Subproject has not been checked out. Run', mlog.bold('meson --reconfigure'), 'to fetch it if needed.')
    else:
        # The subproject has not changed, or the new source and/or patch
        # tarballs should be extracted in the same directory than previous
        # version.
        mlog.log('  -> Subproject has not changed, or the new source/patch needs to be extracted on the same location.\n' +
                 '     In that case, delete', mlog.bold(repo_dir), 'and run', mlog.bold('meson --reconfigure'))
    return True

def git_output(cmd, workingdir):
    return git(cmd, workingdir, check=True, universal_newlines=True,
               stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout

def git_show(repo_dir):
    commit_message = git_output(['show', '--quiet', '--pretty=format:%h%n%d%n%s%n[%an]'], repo_dir)
    parts = [s.strip() for s in commit_message.split('\n')]
    mlog.log('  ->', mlog.yellow(parts[0]), mlog.red(parts[1]), parts[2], mlog.blue(parts[3]))

def update_git(wrap, repo_dir, options):
    if not os.path.isdir(repo_dir):
        mlog.log('  -> Not used.')
        return True
    revision = wrap.get('revision')
    if not revision:
        # It could be a detached git submodule for example.
        mlog.log('  -> No revision specified.')
        return True
    branch = git_output(['branch', '--show-current'], repo_dir).strip()
    if branch == '':
        try:
            # We are currently in detached mode, just checkout the new revision
            git_output(['fetch'], repo_dir)
            git_output(['checkout', revision], repo_dir)
        except subprocess.CalledProcessError as e:
            out = e.output.strip()
            mlog.log('  -> Could not checkout revision', mlog.cyan(revision))
            mlog.log(mlog.red(out))
            mlog.log(mlog.red(str(e)))
            return False
    elif branch == revision:
        try:
            # We are in the same branch, pull latest commits
            git_output(['-c', 'rebase.autoStash=true', 'pull', '--rebase'], repo_dir)
        except subprocess.CalledProcessError as e:
            out = e.output.strip()
            mlog.log('  -> Could not rebase', mlog.bold(repo_dir), 'please fix and try again.')
            mlog.log(mlog.red(out))
            mlog.log(mlog.red(str(e)))
            return False
    else:
        # We are in another branch, probably user created their own branch and
        # we should rebase it on top of wrap's branch.
        if options.rebase:
            try:
                git_output(['fetch'], repo_dir)
                git_output(['-c', 'rebase.autoStash=true', 'rebase', revision], repo_dir)
            except subprocess.CalledProcessError as e:
                out = e.output.strip()
                mlog.log('  -> Could not rebase', mlog.bold(repo_dir), 'please fix and try again.')
                mlog.log(mlog.red(out))
                mlog.log(mlog.red(str(e)))
                return False
        else:
            mlog.log('  -> Target revision is', mlog.bold(revision), 'but currently in branch is', mlog.bold(ret), '\n' +
                     '     To rebase your branch on top of', mlog.bold(revision), 'use', mlog.bold('--rebase'), 'option.')
            return True

    git_output(['submodule', 'update', '--checkout', '--recursive'], repo_dir)
    git_show(repo_dir)
    return True

def update_hg(wrap, repo_dir, options):
    if not os.path.isdir(repo_dir):
        mlog.log('  -> Not used.')
        return True
    revno = wrap.get('revision')
    if revno.lower() == 'tip':
        # Failure to do pull is not a fatal error,
        # because otherwise you can't develop without
        # a working net connection.
        subprocess.call(['hg', 'pull'], cwd=repo_dir)
    else:
        if subprocess.call(['hg', 'checkout', revno], cwd=repo_dir) != 0:
            subprocess.check_call(['hg', 'pull'], cwd=repo_dir)
            subprocess.check_call(['hg', 'checkout', revno], cwd=repo_dir)
    return True

def update_svn(wrap, repo_dir, options):
    if not os.path.isdir(repo_dir):
        mlog.log('  -> Not used.')
        return True
    revno = wrap.get('revision')
    p, out, _ = Popen_safe(['svn', 'info', '--show-item', 'revision', repo_dir])
    current_revno = out
    if current_revno == revno:
        return True
    if revno.lower() == 'head':
        # Failure to do pull is not a fatal error,
        # because otherwise you can't develop without
        # a working net connection.
        subprocess.call(['svn', 'update'], cwd=repo_dir)
    else:
        subprocess.check_call(['svn', 'update', '-r', revno], cwd=repo_dir)
    return True

def update(wrap, repo_dir, options):
    mlog.log('Updating {}...'.format(wrap.name))
    if wrap.type == 'file':
        return update_file(wrap, repo_dir, options)
    elif wrap.type == 'git':
        return update_git(wrap, repo_dir, options)
    elif wrap.type == 'hg':
        return update_hg(wrap, repo_dir, options)
    elif wrap.type == 'svn':
        return update_svn(wrap, repo_dir, options)
    else:
        mlog.log('  -> Cannot update', wrap.type, 'subproject')
    return True

def checkout(wrap, repo_dir, options):
    if wrap.type != 'git' or not os.path.isdir(repo_dir):
        return True
    branch_name = options.branch_name if options.branch_name else wrap.get('revision')
    if not branch_name:
        # It could be a detached git submodule for example.
        return True
    cmd = ['checkout', branch_name, '--']
    if options.b:
        cmd.insert(1, '-b')
    mlog.log('Checkout {} in {}...'.format(branch_name, wrap.name))
    try:
        # Stash any pending changes. Don't use git_output() here because we want
        # the user to see his changes have been saved.
        git(['stash'], repo_dir, check=True, universal_newlines=True)
        git_output(cmd, repo_dir)
        git_show(repo_dir)
    except subprocess.CalledProcessError as e:
        out = e.output.strip()
        mlog.log('  -> ', mlog.red(out))
        return False
    return True

def download(wrap, repo_dir, options):
    mlog.log('Download {}...'.format(wrap.name))
    if os.path.isdir(repo_dir):
        mlog.log('  -> Already downloaded')
        return True
    try:
        r = Resolver(os.path.dirname(repo_dir))
        r.resolve(wrap.name, 'meson')
        mlog.log('  -> done')
    except WrapException as e:
        mlog.log('  ->', mlog.red(str(e)))
        return False
    return True

def foreach(wrap, repo_dir, options):
    mlog.log('Executing command in {}'.format(repo_dir))
    if not os.path.isdir(repo_dir):
        mlog.log('  -> Not downloaded yet')
        return True
    try:
        out = subprocess.check_output([options.command] + options.args,
                                      stderr=subprocess.STDOUT,
                                      cwd=repo_dir).decode()
        mlog.log(out, end='')
    except subprocess.CalledProcessError as e:
        err_message = "Command '{}' returned non-zero exit status {}.".format(" ".join(e.cmd), e.returncode)
        out = e.output.decode()
        mlog.log('  -> ', mlog.red(err_message))
        mlog.log(out, end='')
        return False
    return True

def add_common_arguments(p):
    p.add_argument('--sourcedir', default='.',
                   help='Path to source directory')
    p.add_argument('--type', default='',
                   choices=['file', 'git', 'hg', 'svn'],
                   help='Only subprojects of given type (default: all)')

def add_subprojects_argument(p):
    p.add_argument('subprojects', nargs='*',
                   help='List of subprojects (default: all)')

def add_arguments(parser):
    subparsers = parser.add_subparsers(title='Commands', dest='command')
    subparsers.required = True

    p = subparsers.add_parser('update', help='Update all subprojects from wrap files')
    p.add_argument('--rebase', default=False, action='store_true',
                   help='Rebase your branch on top of wrap\'s revision (git only)')
    add_common_arguments(p)
    add_subprojects_argument(p)
    p.set_defaults(subprojects_func=update)

    p = subparsers.add_parser('checkout', help='Checkout a branch (git only)')
    p.add_argument('-b', default=False, action='store_true',
                   help='Create a new branch')
    p.add_argument('branch_name', nargs='?',
                   help='Name of the branch to checkout or create (default: revision set in wrap file)')
    add_common_arguments(p)
    add_subprojects_argument(p)
    p.set_defaults(subprojects_func=checkout)

    p = subparsers.add_parser('download', help='Ensure subprojects are fetched, even if not in use. ' +
                                               'Already downloaded subprojects are not modified. ' +
                                               'This can be used to pre-fetch all subprojects and avoid downloads during configure.')
    add_common_arguments(p)
    add_subprojects_argument(p)
    p.set_defaults(subprojects_func=download)

    p = subparsers.add_parser('foreach', help='Execute a command in each subproject directory.')
    p.add_argument('command', metavar='command ...',
                   help='Command to execute in each subproject directory')
    p.add_argument('args', nargs=argparse.REMAINDER,
                   help=argparse.SUPPRESS)
    add_common_arguments(p)
    p.set_defaults(subprojects=[])
    p.set_defaults(subprojects_func=foreach)

def run(options):
    src_dir = os.path.relpath(os.path.realpath(options.sourcedir))
    if not os.path.isfile(os.path.join(src_dir, 'meson.build')):
        mlog.error('Directory', mlog.bold(src_dir), 'does not seem to be a Meson source directory.')
        return 1
    subprojects_dir = os.path.join(src_dir, 'subprojects')
    if not os.path.isdir(subprojects_dir):
        mlog.log('Directory', mlog.bold(src_dir), 'does not seem to have subprojects.')
        return 0
    r = Resolver(subprojects_dir)
    if options.subprojects:
        wraps = [wrap for name, wrap in r.wraps.items() if name in options.subprojects]
    else:
        wraps = r.wraps.values()
    failures = []
    for wrap in wraps:
        if options.type and wrap.type != options.type:
            continue
        dirname = os.path.join(subprojects_dir, wrap.directory)
        if not options.subprojects_func(wrap, dirname, options):
            failures.append(wrap.name)
    if failures:
        m = 'Please check logs above as command failed in some subprojects which could have been left in conflict state: '
        m += ', '.join(failures)
        mlog.warning(m)
    return len(failures)
