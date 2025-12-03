"""Doodba child project tasks.

This file is to be executed with https://www.pyinvoke.org/ in Python 3.8.1+.

Contains common helpers to develop using this child project.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import datetime
from glob import iglob
from itertools import chain
from logging import getLogger
from pathlib import Path
from shutil import which

from invoke import exceptions, task

try:
    import yaml
except ImportError:
    from invoke.util import yaml

PROJECT_ROOT = Path(__file__).parent.absolute()
SRC_PATH = PROJECT_ROOT / "odoo" / "custom" / "src"
UID_ENV = {
    "GID": os.environ.get("DOODBA_GID", str(os.getgid())),
    "UID": os.environ.get("DOODBA_UID", str(os.getuid())),
    "DOODBA_UMASK": os.environ.get("DOODBA_UMASK", "27"),
}
UID_ENV.update(
    {
        "DOODBA_GITAGGREGATE_GID": os.environ.get(
            "DOODBA_GITAGGREGATE_GID", UID_ENV["GID"]
        ),
        "DOODBA_GITAGGREGATE_UID": os.environ.get(
            "DOODBA_GITAGGREGATE_UID", UID_ENV["UID"]
        ),
    }
)
SERVICES_WAIT_TIME = int(os.environ.get("SERVICES_WAIT_TIME", 4))
ODOO_VERSION = float(
    yaml.safe_load((PROJECT_ROOT / "common.yaml").read_text())["services"]["odoo"][
        "build"
    ]["args"]["ODOO_VERSION"]
)
# Depending on the user's docker version either version of docker compose could not
# be available. We default to v2 and fallback to v1.
docker_compose_v2 = (
    subprocess.run([shutil.which("docker"), "compose"], capture_output=True).returncode
    == 0
)
DOCKER_COMPOSE_CMD = (
    f"{shutil.which('docker')} compose"
    if docker_compose_v2
    else shutil.which("docker-compose")
)
E2E_COMPOSE_FILES = (
    f"{DOCKER_COMPOSE_CMD} -f docker-compose.yml -f docker-compose.e2e.yml"
)

DEMO_PROFILES = {
    # Standardized demo recipes: extend this map when adding new demos.
    "mis-demo-v2": {
        # Uses consolidated demo stack (spp_demo) plus MIS/Case/GRM demo data
        "modules": "base,spp_demo,spp_mis_demo_v2,spp_case_base,spp_demo_case,spp_grm,spp_grm_demo",
        "project": "spp-mis-demo-v2",
        "description": "MIS demo v2 with CR v2, cycles, GRM stories, Case Management",
        "generate_demo": ["mis_demo_v2", "case_demo", "grm_demo"],
    },
    # Aliases for convenience / new naming
    "demo-mis-v2": {
        "modules": "base,spp_base_demo,spp_mis_demo_v2,spp_case_base,spp_demo_case,spp_grm,spp_grm_demo",
        "project": "spp-mis-demo-v2",
        "description": "Alias for MIS demo v2",
        "generate_demo": ["mis_demo_v2", "case_demo", "grm_demo"],
    },
    "demo_mis_v2": {
        "modules": "base,spp_base_demo,spp_mis_demo_v2,spp_case_base,spp_demo_case,spp_grm,spp_grm_demo",
        "project": "spp-mis-demo-v2",
        "description": "Alias for MIS demo v2 (underscore style)",
        "generate_demo": ["mis_demo_v2", "case_demo", "grm_demo"],
    },
    # GRM-focused demo: builds on MIS demo registrants/programs
    "grm-demo": {
        "modules": "base,spp_base_demo,spp_mis_demo_v2,spp_grm_demo",
        "project": "grm-demo",
        "description": "GRM demo with tickets linked to MIS demo registrants/programs",
        "generate_demo": ["mis_demo_v2", "grm_demo"],
    },
    # Case management demo: builds on MIS registrants
    "case-demo": {
        "modules": "base,spp_base_demo,spp_mis_demo_v2,spp_demo_case",
        "project": "case-demo",
        "description": "Case management demo with stories and volume cases",
        "generate_demo": ["mis_demo_v2", "case_demo"],
    },
}

_logger = getLogger(__name__)


def _override_docker_command(service, command, file, orig_file=None):
    # Read config from main file
    default_compose_file_version = "2.4"
    if orig_file:
        with open(orig_file) as fd:
            orig_docker_config = yaml.safe_load(fd.read())
            docker_compose_file_version = orig_docker_config.get(
                "version", default_compose_file_version
            )
    else:
        docker_compose_file_version = default_compose_file_version
    docker_config = {
        "version": docker_compose_file_version,
        "services": {service: {"command": command}},
    }
    docker_config_yaml = yaml.dump(docker_config)
    file.write(docker_config_yaml)
    file.flush()


def _remove_auto_reload(file, orig_file):
    with open(orig_file) as fd:
        orig_docker_config = yaml.safe_load(fd.read())
    odoo_command = orig_docker_config["services"]["odoo"]["command"]
    new_odoo_command = []
    for flag in odoo_command:
        if flag.startswith("--dev"):
            flag = flag.replace("reload,", "")
        new_odoo_command.append(flag)
    _override_docker_command("odoo", new_odoo_command, file, orig_file=orig_file)


def _get_cwd_addon(file):
    cwd = Path(file).resolve()
    manifest_file = False
    while PROJECT_ROOT < cwd:
        manifest_file = (cwd / "__manifest__.py").exists() or (
            cwd / "__openerp__.py"
        ).exists()
        if manifest_file:
            return cwd.stem
        cwd = cwd.parent
        if cwd == PROJECT_ROOT:
            return None


def _scan_subrepos_and_add_path_mappings(
    cw_config,
    debugpy_configuration,
    firefox_configuration,
    chrome_configuration,
):
    """Scan subrepos in SRC_PATH, configure folders & pathMappings."""
    for subrepo in SRC_PATH.glob("*"):
        if not subrepo.is_dir():
            continue
        if (subrepo / ".git").exists() and subrepo.name != "odoo":
            cw_config["folders"].append(
                {"path": str(subrepo.relative_to(PROJECT_ROOT))}
            )

        # Check if subrepo is itself a doodba-copier project
        is_doodba_subproject = False
        answers_file = subrepo / ".copier-answers.yml"
        if answers_file.is_file():
            with answers_file.open() as f:
                answers = yaml.safe_load(f) or {}
            if "Tecnativa/doodba-copier-template" in answers.get("_src_path", ""):
                is_doodba_subproject = True

        private_dir = subrepo / "odoo" / "custom" / "src" / "private"
        # Default scanning approach (1-level + addons/* + private/*)
        for addon in chain(
            subrepo.glob("*"),
            subrepo.glob("addons/*"),
            private_dir.glob("*"),
        ):
            if (addon / "__manifest__.py").is_file() or (
                addon / "__openerp__.py"
            ).is_file():
                if is_doodba_subproject:
                    local_path = "${workspaceFolder:%s}/odoo/custom/src/private/%s" % (  # noqa: UP031
                        subrepo.name,
                        addon.name,
                    )
                elif subrepo.name == "odoo":
                    local_path = "${workspaceFolder:%s}/addons/%s/" % (  # noqa: UP031
                        subrepo.name,
                        addon.name,
                    )
                else:
                    local_path = "${workspaceFolder:%s}/%s" % (  # noqa: UP031
                        subrepo.name,
                        addon.name,
                    )
                debugpy_configuration["pathMappings"].append(
                    {
                        "localRoot": local_path,
                        "remoteRoot": f"/opt/odoo/auto/addons/{addon.name}/",
                    }
                )
                url = f"http://localhost:{ODOO_VERSION:.0f}069/{addon.name}/static/"
                path = "${workspaceFolder:%s}/%s/static/" % (  # noqa: UP031
                    subrepo.name,
                    addon.relative_to(subrepo),
                )
                firefox_configuration["pathMappings"].append({"url": url, "path": path})
                chrome_configuration["pathMapping"][url] = path


@task
def write_code_workspace_file(c, cw_path=None):
    """Generate code-workspace file definition.

    Some other tasks will call this one when needed, and since you cannot specify
    the file name there, if you want a specific one, you should call this task
    before.

    Most times you just can forget about this task and let it be run automatically
    whenever needed.

    If you don't define a workspace name, this task will reuse the 1st
    `doodba.*.code-workspace` file found inside the current directory.
    If none is found, it will default to `doodba.$(basename $PWD).code-workspace`.

    If you define it manually, remember to use the same prefix and suffix if you
    want it git-ignored by default.
    Example: `--cw-path doodba.my-custom-name.code-workspace`
    """
    root_name = f"doodba.{PROJECT_ROOT.name}"
    root_var = "${workspaceFolder:%s}" % root_name  # noqa: UP031
    if not cw_path:
        try:
            cw_path = next(PROJECT_ROOT.glob("doodba.*.code-workspace"))
        except StopIteration:
            cw_path = f"{root_name}.code-workspace"
    if not Path(cw_path).is_absolute():
        cw_path = PROJECT_ROOT / cw_path
    cw_config = {}
    try:
        with open(cw_path) as cw_fd:
            cw_config = json.load(cw_fd)
    except (FileNotFoundError, json.decoder.JSONDecodeError):
        pass  # Nevermind, we start with a new config
    # Static settings
    cw_config.setdefault("settings", {})
    cw_config["settings"].update(
        {
            "python.autoComplete.extraPaths": [f"{str(SRC_PATH)}/odoo"],
            "python.analysis.extraPaths": [f"{str(SRC_PATH)}/odoo"],
            "python.formatting.provider": "none",
            "python.linting.flake8Enabled": True,
            "python.linting.ignorePatterns": [f"{str(SRC_PATH)}/odoo/**/*.py"],
            "python.linting.pylintArgs": [
                f"--init-hook=\"import sys;sys.path.append('{str(SRC_PATH)}/odoo')\"",
                "--load-plugins=pylint_odoo",
            ],
            "python.linting.pylintEnabled": True,
            "python.defaultInterpreterPath": "python%s"
            % (2 if ODOO_VERSION < 11 else 3),
            "restructuredtext.confPath": "",
            "search.followSymlinks": False,
            "search.useIgnoreFiles": False,
            # Language-specific configurations
            "[python]": {"editor.defaultFormatter": "ms-python.black-formatter"},
            "[json]": {"editor.defaultFormatter": "esbenp.prettier-vscode"},
            "[jsonc]": {"editor.defaultFormatter": "esbenp.prettier-vscode"},
            "[markdown]": {"editor.defaultFormatter": "esbenp.prettier-vscode"},
            "[yaml]": {"editor.defaultFormatter": "esbenp.prettier-vscode"},
            "[xml]": {"editor.formatOnSave": False},
        }
    )
    # Launch configurations
    debugpy_configuration = {
        "name": "Attach Python debugger to running container",
        "type": "python",
        "request": "attach",
        "pathMappings": [],
        "port": int(ODOO_VERSION) * 1000 + 899,
        # HACK https://github.com/microsoft/vscode-python/issues/14820
        "host": "0.0.0.0",
    }
    firefox_configuration = {
        "type": "firefox",
        "request": "launch",
        "reAttach": True,
        "name": "Connect to firefox debugger",
        "url": f"http://localhost:{ODOO_VERSION:.0f}069/?debug=assets",
        "reloadOnChange": {
            "watch": f"{root_var}/odoo/custom/src/**/*.{'{js,css,scss,less}'}"
        },
        "skipFiles": ["**/lib/**"],
        "pathMappings": [],
    }
    chrome_executable = which("chrome") or which("chromium")
    chrome_configuration = {
        "type": "chrome",
        "request": "launch",
        "name": "Connect to chrome debugger",
        "url": f"http://localhost:{ODOO_VERSION:.0f}069/?debug=assets",
        "skipFiles": ["**/lib/**"],
        "trace": True,
        "pathMapping": {},
    }
    if chrome_executable:
        chrome_configuration["runtimeExecutable"] = chrome_executable

    cw_config["launch"] = {
        "compounds": [
            {
                "name": "Start Odoo and debug Python",
                "configurations": ["Attach Python debugger to running container"],
                "preLaunchTask": "Start Odoo in debug mode",
            },
            {
                "name": "Test and debug current module",
                "configurations": ["Attach Python debugger to running container"],
                "preLaunchTask": "Run Odoo Tests in debug mode for current module",
                "internalConsoleOptions": "openOnSessionStart",
            },
        ],
        "configurations": [
            debugpy_configuration,
            firefox_configuration,
            chrome_configuration,
        ],
    }
    # Configure pathMappings for the main odoo folder
    debugpy_configuration["pathMappings"].append(
        {
            "localRoot": "${workspaceFolder:odoo}/",
            "remoteRoot": "/opt/odoo/custom/src/odoo",
        }
    )
    cw_config["folders"] = []
    _scan_subrepos_and_add_path_mappings(
        cw_config,
        debugpy_configuration,
        firefox_configuration,
        chrome_configuration,
    )

    cw_config["tasks"] = {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "Start Odoo",
                "type": "process",
                "command": "invoke",
                "args": ["start", "--detach"],
                "presentation": {
                    "echo": True,
                    "reveal": "silent",
                    "focus": False,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {"statusbar": {"label": "$(play-circle) Start Odoo"}},
            },
            {
                "label": "Install current module",
                "type": "process",
                "command": "invoke",
                "args": ["install", "--cur-file", "${file}", "restart"],
                "presentation": {
                    "echo": True,
                    "reveal": "always",
                    "focus": True,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {
                    "statusbar": {"label": "$(symbol-property) Install module"}
                },
            },
            {
                "label": "Run Odoo Tests for current module",
                "type": "process",
                "command": "invoke",
                "args": ["test", "--cur-file", "${file}"],
                "presentation": {
                    "echo": True,
                    "reveal": "always",
                    "focus": True,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {"statusbar": {"label": "$(beaker) Test module"}},
            },
            {
                "label": "Run Odoo Tests in debug mode for current module",
                "type": "process",
                "command": "invoke",
                "args": [
                    "test",
                    "--cur-file",
                    "${file}",
                    "--debugpy",
                ],
                "presentation": {
                    "echo": True,
                    "reveal": "silent",
                    "focus": False,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {"statusbar": {"hide": True}},
            },
            {
                "label": "Start Odoo in debug mode",
                "type": "process",
                "command": "invoke",
                "args": ["start", "--detach", "--debugpy"],
                "presentation": {
                    "echo": True,
                    "reveal": "silent",
                    "focus": False,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {"statusbar": {"hide": True}},
            },
            {
                "label": "Stop Odoo",
                "type": "process",
                "command": "invoke",
                "args": ["stop"],
                "presentation": {
                    "echo": True,
                    "reveal": "silent",
                    "focus": False,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {"statusbar": {"label": "$(stop-circle) Stop Odoo"}},
            },
            {
                "label": "Restart Odoo",
                "type": "process",
                "command": "invoke",
                "args": ["restart"],
                "presentation": {
                    "echo": True,
                    "reveal": "silent",
                    "focus": False,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {"statusbar": {"label": "$(history) Restart Odoo"}},
            },
            {
                "label": "See container logs",
                "type": "process",
                "command": "invoke",
                "args": ["logs"],
                "presentation": {
                    "echo": True,
                    "reveal": "always",
                    "focus": False,
                    "panel": "shared",
                    "showReuseMessage": True,
                    "clear": False,
                },
                "problemMatcher": [],
                "options": {
                    "statusbar": {"label": "$(list-selection) See container logs"}
                },
            },
        ],
    }
    # Sort project folders
    cw_config["folders"].sort(key=lambda x: x["path"])
    # Put Odoo folder just before private and top folder and map to debugpy
    odoo = SRC_PATH / "odoo"
    if odoo.is_dir():
        cw_config["folders"].append({"path": str(odoo.relative_to(PROJECT_ROOT))})
    # HACK https://github.com/microsoft/vscode/issues/95963 put private second to last
    private = SRC_PATH / "private"
    if private.is_dir():
        cw_config["folders"].append({"path": str(private.relative_to(PROJECT_ROOT))})
    # HACK https://github.com/microsoft/vscode/issues/37947 put top folder last
    cw_config["folders"].append({"path": ".", "name": root_name})
    with open(cw_path, "w") as cw_fd:
        json.dump(cw_config, cw_fd, indent=2)
        cw_fd.write("\n")


@task
def develop(c):
    """Set up a basic development environment."""
    # Prepare environment
    auto = Path(PROJECT_ROOT, "odoo", "auto")
    addons = auto / "addons"
    addons.mkdir(parents=True, exist_ok=True)
    # Allow others writing, for podman support
    auto.chmod(0o777)
    addons.chmod(0o777)
    with c.cd(str(PROJECT_ROOT)):
        c.run("git init")
        c.run("ln -sf devel.yaml docker-compose.yml")
        write_code_workspace_file(c)
        c.run("pre-commit install")


@task(develop)
def git_aggregate(c):
    """Download odoo & addons git code.

    Executes git-aggregator from within the doodba container.
    """
    with c.cd(str(PROJECT_ROOT)):
        c.run(
            DOCKER_COMPOSE_CMD + " --file setup-devel.yaml run --rm -T odoo",
            env=UID_ENV,
        )
    write_code_workspace_file(c)
    for git_folder in SRC_PATH.glob("*/.git/.."):
        action = (
            "install"
            if (git_folder / ".pre-commit-config.yaml").is_file()
            else "uninstall"
        )
        with c.cd(str(git_folder)):
            c.run(f"pre-commit {action}")


@task(develop)
def git_aggregate_host(c):
    """Download odoo & addons git code directly on the host.

    Executes git-aggregator on the host machine, avoiding Docker SSH agent issues.
    """
    # Define paths
    src_path = PROJECT_ROOT / "odoo" / "custom" / "src"
    repos_yaml = src_path / "repos.yaml"

    # Create src directory if it doesn't exist
    src_path.mkdir(parents=True, exist_ok=True)

    # Print the paths being used for clarity
    _logger.info("Project root: %s", PROJECT_ROOT)
    _logger.info("Source path: %s", src_path)
    _logger.info("Repos YAML: %s", repos_yaml)

    # Define environment variables used in repos.yaml
    env = {
        "DEPTH_DEFAULT": "1",  # Default depth for shallow clones
        "DEPTH_MERGE": "100",  # Depth when merging PRs
        "ODOO_VERSION": f"{ODOO_VERSION:.1f}",  # Make available for repos.yaml substitution
        "PATH": os.environ.get("PATH", ""),
    }

    # Important: Change directory to src_path before running git-aggregator
    _logger.info(
        "\nRunning git-aggregator from the src directory to ensure correct repository placement..."
    )
    with c.cd(str(src_path)):
        # Run git-aggregator
        _logger.info("Starting git-aggregator...")
        try:
            # Use the relative path to repos.yaml (just the filename when in the same directory)
            result = c.run(
                "gitaggregate -c repos.yaml --expand-env aggregate",
                env=env,
                pty=True,
            )

            if result.ok:
                _logger.info("\nGit aggregation completed successfully!")
                _logger.info("Repositories have been cloned into: %s", src_path)
            else:
                _logger.info("\nGit aggregation failed. Check the errors above.")
        except Exception as e:
            _logger.info("\nError running git-aggregator: %s", e)
            _logger.info("\nTrying without environment variable expansion...")
            try:
                result = c.run("gitaggregate -c repos.yaml aggregate", pty=True)
                if result.ok:
                    _logger.info("\nGit aggregation completed successfully!")
                    _logger.info("Repositories have been cloned into: %s", src_path)
            except Exception as e2:
                _logger.info(
                    "\nError running git-aggregator without env expansion: %s", e2
                )

    write_code_workspace_file(c)
    for git_folder in SRC_PATH.glob("*/.git/.."):
        action = (
            "install"
            if (git_folder / ".pre-commit-config.yaml").is_file()
            else "uninstall"
        )
        with c.cd(str(git_folder)):
            c.run(f"pre-commit {action}")


@task(develop)
def closed_prs(c):
    """Test closed PRs from repos.yaml"""
    with c.cd(str(PROJECT_ROOT / "odoo/custom/src")):
        cmd = "gitaggregate -c {} show-closed-prs".format("repos.yaml")
        c.run(cmd, env=UID_ENV, pty=True)


@task()
def img_build(c, pull=True):
    """Build docker images."""
    cmd = DOCKER_COMPOSE_CMD + " build"
    if pull:
        cmd += " --pull"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, env=UID_ENV, pty=True)


@task()
def img_pull(c):
    """Pull docker images."""
    with c.cd(str(PROJECT_ROOT)):
        c.run(DOCKER_COMPOSE_CMD + " pull", pty=True)


@task()
def lint(c, verbose=False):
    """Lint & format source code."""
    cmd = "pre-commit run --show-diff-on-failure --all-files --color=always"
    if verbose:
        cmd += " --verbose"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd)


@task()
def start(c, detach=True, debugpy=False, _reload=True, port_prefix=0):
    """Start environment."""
    cmd = DOCKER_COMPOSE_CMD + " up"
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
    ) as tmp_docker_compose_file:
        if debugpy or not _reload:
            # Remove auto-reload
            cmd = (
                DOCKER_COMPOSE_CMD + " -f docker-compose.yml "
                f"-f {tmp_docker_compose_file.name} up"
            )
            _remove_auto_reload(
                tmp_docker_compose_file,
                orig_file=PROJECT_ROOT / "docker-compose.yml",
            )
        if detach:
            cmd += " --detach"
        with c.cd(str(PROJECT_ROOT)):
            env = dict(
                UID_ENV,
                DOODBA_DEBUGPY_ENABLE=str(int(debugpy)),
            )
            if port_prefix:
                env["PORT_PREFIX"] = str(port_prefix)
            result = c.run(
                cmd,
                pty=True,
                env=env,
            )
            if not (
                "Recreating" in result.stdout
                or "Starting" in result.stdout
                or "Creating" in result.stdout
            ):
                restart(c)
        _logger.info("Waiting for services to spin up...")
        time.sleep(SERVICES_WAIT_TIME)


@task(
    help={
        "modules": "Comma-separated list of modules to install.",
        "dbname": "Target database name (defaults to PGDATABASE/devel).",
        "core": "Install all core addons. Default: False",
        "extra": "Install all extra addons. Default: False",
        "private": "Install all private addons. Default: False",
        "enterprise": "Install all enterprise addons. Default: False",
        "cur-file": "Path to the current file. Addon name will be obtained from there to install.",
    },
)
def install(
    c,
    modules=None,
    cur_file=None,
    core=False,
    extra=False,
    private=False,
    enterprise=False,
    dbname=None,
):
    """Install Odoo addons

    By default, installs addon from directory being worked on,
    unless other options are specified.
    """
    if not (modules or core or extra or private or enterprise):
        cur_module = _get_cwd_addon(cur_file or Path.cwd())
        if not cur_module:
            raise exceptions.ParseError(
                msg="Odoo addon to install not found. "
                "You must provide at least one option for modules"
                " or be in a subdirectory of one."
                " See --help for details."
            )
        modules = cur_module
    target_db = dbname or os.environ.get("PGDATABASE") or "devel"
    # Prefer direct Odoo CLI with explicit DB when modules are provided
    if modules and not (core or extra or private or enterprise):
        cmd = (
            DOCKER_COMPOSE_CMD
            + f" run --rm -e DB_FILTER=^{target_db}$ odoo odoo --stop-after-init -d {target_db} -i {modules}"
        )
    else:
        cmd = DOCKER_COMPOSE_CMD + " run --rm odoo addons init"
        if core:
            cmd += " --core"
        if extra:
            cmd += " --extra"
        if private:
            cmd += " --private"
        if enterprise:
            cmd += " --enterprise"
        if modules:
            cmd += f" -w {modules}"
    with c.cd(str(PROJECT_ROOT)):
        c.run(DOCKER_COMPOSE_CMD + " stop odoo")
        c.run(
            cmd,
            env=UID_ENV,
            pty=True,
        )


@task(
    help={
        "module": "Specific Odoo module to update.",
        "all": "Update all modules. Takes a lot of time. [default: False]",
        "repo": "Update all modules from a specific repository.",
        "msgmerge": "Merge .pot changes into all .po files. [default: True]",
        "fuzzy_matching": "Use fuzzy matching when merging. [default: False]",
        "purge_old_translations": "Remove lines with old translations. [default: True]",
        "remove_dates": "Remove dates from .po files. [default: True]",
    }
)
def updatepot(
    c,
    module=None,
    _all=False,
    repo=None,
    msgmerge=True,
    fuzzy_matching=False,
    purge_old_translations=True,
    remove_dates=True,
):
    """Updates POT of a given module"""
    if not module and not _all and not repo:
        cur_module = _get_cwd_addon(Path.cwd())
        if not cur_module:
            raise exceptions.ParseError(
                msg="Odoo addon to update translation not found "
                "You must provide at least one of: -m {module}, "
                "be in the subdirectory of a module, --all or -r {repo} "
                "See --help for details."
            )
        module = cur_module

    cmd = (
        DOCKER_COMPOSE_CMD
        + f" run --rm  -v {PROJECT_ROOT}/odoo/custom:/tmp/odoo/custom:rw,z "
        f"-v {PROJECT_ROOT}/odoo/auto:/tmp/odoo/auto:rw,z odoo "
        "click-odoo-makepot --addons-dir "
        f"{'/tmp/odoo/auto/addons' if not repo else '/tmp/odoo/custom/src/' + repo}/"
    )

    cmd += " --msgmerge" if msgmerge else " --no-msgmerge"
    cmd += " --no-fuzzy-matching" if not fuzzy_matching else " --fuzzy-matching"
    cmd += (
        " --purge-old-translations"
        if purge_old_translations
        else " --no-purge-old-translations"
    )
    if not _all and not repo:
        cmd += f" -m {module}"

    with c.cd(str(PROJECT_ROOT)):
        c.run(DOCKER_COMPOSE_CMD + " stop odoo")
        c.run(
            cmd,
            env=UID_ENV,
            pty=True,
        )
    glob = (
        f"{PROJECT_ROOT}/odoo/custom/src/{'*' if not repo else repo}"
        f"/{'*' if _all or repo else module}/i18n/"
    )
    new_files = iglob(f"{glob}/*.po*")
    for new_file in new_files:
        file_name = os.path.basename(new_file)
        if file_name.endswith("~"):
            os.remove(new_file)
            continue
        with open(new_file) as fd:
            content = fd.read()
        new_lines = []
        for line in content.splitlines():
            if remove_dates and (
                line.startswith('"POT-Creation-Date')
                or line.startswith('"PO-Revision-Date')
            ):
                continue
            new_lines.append(line)
        content = "\n".join(new_lines)
        with open(new_file, "w") as fd:
            fd.write(content.strip() + "\n")
    _logger.info(".po[t] files updated")
    precommit_cmd = (
        f"pre-commit run --files {' '.join(iglob(f'{glob}/*.po*'))}--color=always"
    )
    if not repo and module:
        for folder in iglob(f"{PROJECT_ROOT}/odoo/custom/src/*/*"):
            if os.path.isdir(folder) and os.path.basename(folder) == module:
                repo = os.path.basename(os.path.dirname(folder))
                break
    precommit_folder = (
        str(PROJECT_ROOT) + f"/odoo/custom/src/{repo}" if repo != "private" else ""
    )
    with c.cd(str(precommit_folder)):
        c.run(precommit_cmd)


@task(
    help={
        "modules": "Comma-separated list of modules to uninstall.",
    },
)
def uninstall(
    c,
    modules=None,
    cur_file=None,
):
    """Uninstall Odoo addons

    By default, uninstalls addon from directory being worked on,
    unless other options are specified.
    """
    if not modules:
        cur_module = _get_cwd_addon(cur_file or Path.cwd())
        if not cur_module:
            raise exceptions.ParseError(
                msg="Odoo addon to uninstall not found. "
                "You must provide at least one option for modules"
                " or be in a subdirectory of one."
                " See --help for details."
            )
        modules = cur_module
    cmd = (
        DOCKER_COMPOSE_CMD
        + f" run --rm odoo click-odoo-uninstall -m {modules or cur_module}"
    )
    with c.cd(str(PROJECT_ROOT)):
        c.run(
            cmd,
            env=UID_ENV,
            pty=True,
        )


def _get_module_dependencies(
    c, modules=None, core=False, extra=False, private=False, enterprise=False
):
    """Returns a list of the addons' dependencies

    By default, refers to the addon from directory being worked on,
    unless other options are specified.
    """
    # Get list of dependencies for addon
    cmd = DOCKER_COMPOSE_CMD + " run --rm odoo addons list --dependencies"
    if core:
        cmd += " --core"
    if extra:
        cmd += " --extra"
    if private:
        cmd += " --private"
    if enterprise:
        cmd += " --enterprise"
    if modules:
        cmd += f" -w {modules}"
    with c.cd(str(PROJECT_ROOT)):
        dependencies = c.run(
            cmd,
            env=UID_ENV,
            hide="stdout",
        ).stdout.splitlines()[-1]
    return dependencies


def _test_in_debug_mode(c, odoo_command):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml"
    ) as tmp_docker_compose_file:
        cmd = (
            DOCKER_COMPOSE_CMD + " -f docker-compose.yml "
            f"-f {tmp_docker_compose_file.name} up -d"
        )
        _override_docker_command(
            "odoo",
            odoo_command,
            file=tmp_docker_compose_file,
            orig_file=Path(str(PROJECT_ROOT), "docker-compose.yml"),
        )
        with c.cd(str(PROJECT_ROOT)):
            c.run(
                cmd,
                env=dict(
                    UID_ENV,
                    DOODBA_DEBUGPY_ENABLE="1",
                ),
                pty=True,
            )
        _logger.info("Waiting for services to spin up...")
        time.sleep(SERVICES_WAIT_TIME)


def _sanitize_filename(fragment: str) -> str:
    """Return a filesystem-friendly fragment for log/xunit filenames."""

    if not fragment:
        return "odoo-tests"
    fragment = fragment.replace(",", "-")
    fragment = re.sub(r"[^A-Za-z0-9_.-]", "_", fragment)
    return fragment[:80]  # keep names readable while avoiding path issues


def _summarize_test_results(log_path: Path):
    """Return a compact summary parsed from the log."""

    summary_lines = []

    if not log_path.exists():
        summary_lines.append(f"Log not found; summary skipped: {log_path}")
        summary_lines.append(f"Full log: {log_path}")
        return summary_lines

    try:
        lines = log_path.read_text(errors="ignore").splitlines()
    except OSError as exc:  # pragma: no cover - defensive
        summary_lines.append(f"Could not read log {log_path}: {exc}")
        summary_lines.append(f"Full log: {log_path}")
        return summary_lines

    log_text = "\n".join(lines)

    tests = None
    failures = errors = skipped = 0
    tests_not_run = False

    ran = re.search(r"Ran (\d+) tests? in", log_text)
    if ran:
        tests = int(ran.group(1))
    else:
        odoo_line = re.search(
            r"(\d+) failed, (\d+) error\(s\) of (\d+) tests", log_text
        )
        if odoo_line:
            failures = int(odoo_line.group(1))
            errors = int(odoo_line.group(2))
            tests = int(odoo_line.group(3))
            if tests == 0:
                tests_not_run = True
        else:
            stats_total = 0
            for line in lines:
                m = re.search(r"odoo\.tests\.stats: .*?: (\d+) tests", line)
                if m:
                    stats_total += int(m.group(1))
            if stats_total:
                tests = stats_total

    fail_summary = re.search(
        r"FAILED \(failures=(\d+), errors=(\d+)(?:, skipped=(\d+))?", log_text
    )
    ok_summary = re.search(r"^OK(?: \(([^)]*)\))?$", log_text, re.MULTILINE)

    if fail_summary:
        failures = int(fail_summary.group(1))
        errors = int(fail_summary.group(2))
        skipped = int(fail_summary.group(3) or 0)
    elif ok_summary:
        details = ok_summary.group(1) or ""
        skipped_match = re.search(r"skipped=(\d+)", details)
        skipped = int(skipped_match.group(1)) if skipped_match else 0
    # else leave defaults; some failures may prevent summary printing

    if tests is not None:
        passed = tests - failures - errors - skipped
    else:
        passed = None

    failing = []
    # Look for test failure patterns in Odoo test output
    # Pattern: "ERROR: TestClass.test_method" or "FAIL: TestClass.test_method"
    i = 0
    while i < len(lines):
        line = lines[i]
        # Pattern: "ERROR: TestClass.test_method" or "FAIL: TestClass.test_method"
        error_match = re.search(
            r"(?:ERROR|FAIL):\s*([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]+)", line
        )
        if error_match:
            test_name = error_match.group(1)
            error_msg = None
            error_type = None

            # Extract module name - try multiple strategies
            module_name = None

            # Strategy 1: Look in the current line (e.g., "odoo.addons.spp_api_v2.tests.test_consent")
            # Note: module names can contain numbers, so use [a-z0-9_]+
            module_match = re.search(r"odoo\.addons\.([a-z0-9_]+)\.tests", line)
            if module_match:
                module_name = module_match.group(1)

            # Strategy 2: Look backwards for "Starting" lines (usually 1-10 lines back)
            if not module_name:
                for lookback_idx in range(max(0, i - 10), i):
                    lookback_line = lines[lookback_idx]
                    # Pattern: "Starting TestClass.test_method" with module info
                    if "Starting" in lookback_line:
                        module_match = re.search(
                            r"odoo\.addons\.([a-z0-9_]+)\.tests", lookback_line
                        )
                        if module_match:
                            module_name = module_match.group(1)
                            break

            # Strategy 3: Look ahead in traceback for file paths
            if not module_name:
                for j in range(i + 1, min(i + 15, len(lines))):
                    lookahead_line = lines[j]
                    # Pattern: File "/opt/odoo/auto/addons/spp_api_v2/tests/..."
                    file_match = re.search(
                        r"/opt/odoo/auto/addons/([a-z0-9_]+)/tests/", lookahead_line
                    )
                    if file_match:
                        module_name = file_match.group(1)
                        break
                    # Also check custom path
                    file_match = re.search(
                        r"/opt/odoo/custom/src/([^/]+)/.*tests/", lookahead_line
                    )
                    if file_match:
                        # For openspp_modules, extract the actual module name from path
                        path_part = file_match.group(1)
                        if path_part == "openspp_modules":
                            # Try to extract module from further in the path
                            deeper_match = re.search(
                                r"openspp_modules/([a-z0-9_]+)/", lookahead_line
                            )
                            if deeper_match:
                                module_name = deeper_match.group(1)
                                break
                        else:
                            module_name = path_part
                            break

            # Strategy 4: Infer from test class name (fallback heuristic)
            if not module_name:
                # Common patterns: TestConsent -> spp_api_v2, TestVocabulary -> spp_vocabulary
                test_class = test_name.split(".")[0]
                if any(
                    x in test_class
                    for x in [
                        "Consent",
                        "API",
                        "OAuth",
                        "Metadata",
                        "Group",
                        "Individual",
                        "Search",
                    ]
                ):
                    module_name = "spp_api_v2"
                elif "Vocabulary" in test_class:
                    module_name = "spp_vocabulary"
                elif "Registrant" in test_class or "Phone" in test_class:
                    module_name = "spp_registry_base"
                elif "Membership" in test_class:
                    module_name = "spp_registry_membership"
                elif "Record" in test_class and "Consent" in test_class:
                    module_name = "spp_consent"

            # Look ahead for error details (usually within next 25 lines)
            for j in range(i + 1, min(i + 30, len(lines))):
                lookahead_line = lines[j]

                # Check for AssertionError
                if "AssertionError:" in lookahead_line:
                    error_type = "AssertionError"
                    # Extract the assertion message (everything after "AssertionError: ")
                    msg_match = re.search(
                        r"AssertionError:\s*(.+?)(?:\s*$|\s*:)", lookahead_line
                    )
                    if msg_match:
                        error_msg = msg_match.group(1).strip()
                        # If it's a long message, try to get just the key part
                        if len(error_msg) > 150:
                            # Try to extract the comparison part (e.g., "500 != 200")
                            comp_match = re.search(r"(\d+\s*!=\s*\d+)", error_msg)
                            if comp_match:
                                error_msg = comp_match.group(1)
                            else:
                                # Extract first meaningful part
                                parts = error_msg.split(":")
                                if len(parts) > 1:
                                    error_msg = (
                                        parts[0].strip() + ": " + parts[1].strip()[:80]
                                    )
                                else:
                                    error_msg = error_msg[:100] + "..."
                    break

                # Check for other error types (AttributeError, TypeError, KeyError, ValueError, etc.)
                elif re.search(r"^(\w+Error):\s*(.+)$", lookahead_line):
                    match = re.search(r"^(\w+Error):\s*(.+)$", lookahead_line)
                    error_type = match.group(1)
                    error_msg = match.group(2).strip()
                    # Truncate long messages
                    if len(error_msg) > 100:
                        error_msg = error_msg[:97] + "..."
                    break

                # Check for database constraint errors (common in Odoo)
                elif "duplicate key value violates unique constraint" in lookahead_line:
                    error_type = "DatabaseError"
                    constraint_match = re.search(r'"([^"]+)"', lookahead_line)
                    if constraint_match:
                        error_msg = (
                            f"Unique constraint violation: {constraint_match.group(1)}"
                        )
                    else:
                        error_msg = "Unique constraint violation"
                    break

                # Check for NameError, ImportError, etc. in traceback
                elif re.search(r"(\w+Error):", lookahead_line) and not error_type:
                    error_match = re.search(
                        r"(\w+Error):\s*(.+?)(?:\s*$|$)", lookahead_line
                    )
                    if error_match:
                        error_type = error_match.group(1)
                        error_msg = (
                            error_match.group(2).strip()
                            if error_match.group(2)
                            else None
                        )
                        if error_msg and len(error_msg) > 100:
                            error_msg = error_msg[:97] + "..."
                        break

            # If no error type found, try to extract from traceback or default to "Error"
            if not error_type:
                # Look for any error pattern in the next few lines
                for j in range(i + 1, min(i + 10, len(lines))):
                    lookahead_line = lines[j]
                    if "Traceback" in lookahead_line:
                        # Traceback found, error details should follow
                        error_type = "Error"
                        break
                if not error_type:
                    error_type = "Error"

            # Add to failing list
            failing.append(
                {
                    "test": test_name,
                    "module": module_name,
                    "error_type": error_type,
                    "error_msg": error_msg,
                    "line": i + 1,  # Line numbers are 1-indexed
                }
            )

            # Skip ahead to avoid duplicate matches
            i += 5
        else:
            i += 1

    # Build summary with metrics
    summary_parts = []
    if tests is not None:
        summary_parts.append(f"{tests} total")
        if passed is not None:
            summary_parts.append(f"{passed} passed")
            if tests > 0:
                pass_rate = (passed / tests) * 100
                summary_parts.append(f"{pass_rate:.1f}% pass rate")
    summary_parts.append(f"{failures} failed")
    summary_parts.append(f"{errors} errors")
    summary_parts.append(f"{skipped} skipped")

    summary_lines.append("=" * 80)
    summary_lines.append("TEST SUMMARY")
    summary_lines.append("=" * 80)
    summary_lines.append(" | ".join(summary_parts))

    if tests_not_run or tests == 0:
        summary_lines.append(
            "\n⚠️  No tests collected or executed. Install may have failed or no tags matched; see log for details."
        )
    elif tests is None:
        summary_lines.append(
            "\n⚠️  No test results found in log; run may have aborted before tests. See log for details."
        )

    # List failing tests with more detail
    if failing:
        # Remove duplicates (same test name)
        seen_tests = set()
        unique_failing = []
        for fail in failing:
            test_key = fail["test"]
            if test_key not in seen_tests:
                seen_tests.add(test_key)
                unique_failing.append(fail)

        # Group by module for better readability
        by_module = {}
        for fail in unique_failing:
            module = fail.get("module") or "unknown"
            if module not in by_module:
                by_module[module] = []
            by_module[module].append(fail)

        # Add module breakdown summary
        summary_lines.append(f"\n❌ FAILING TESTS ({len(unique_failing)}):")
        if len(by_module) > 1:
            module_summary = []
            for module in sorted(by_module.keys()):
                count = len(by_module[module])
                module_summary.append(f"{module}: {count}")
            summary_lines.append("  " + " | ".join(module_summary))
        summary_lines.append("-" * 80)

        # Sort modules alphabetically
        for module in sorted(by_module.keys()):
            module_failures = by_module[module]
            if len(by_module) > 1:
                summary_lines.append(
                    f"\n  📦 {module} ({len(module_failures)} failures)"
                )

            for fail in module_failures:
                test_name = fail["test"]
                error_type = fail.get("error_type", "Error")
                error_msg = fail.get("error_msg")
                line_no = fail["line"]

                # Format test name (show class.method)
                display_name = test_name
                if "." in test_name:
                    parts = test_name.split(".")
                    if len(parts) >= 2:
                        display_name = f"{parts[-2]}.{parts[-1]}"

                # Format output
                if error_msg:
                    # Truncate very long error messages for display
                    display_msg = error_msg
                    if len(display_msg) > 120:
                        display_msg = display_msg[:117] + "..."
                    summary_lines.append(f"    • {display_name}")
                    summary_lines.append(f"      [{error_type}] {display_msg}")
                else:
                    summary_lines.append(f"    • {display_name} [{error_type}]")

                summary_lines.append(f"      → log line {line_no}")
    elif tests is not None and failures == 0 and errors == 0:
        summary_lines.append("\n✅ All tests passed!")

    summary_lines.append("\n" + "=" * 80)
    summary_lines.append(f"Full log: {log_path}")
    summary_lines.append("=" * 80)
    return summary_lines


def _summarize_resetdb_results(log_path: Path, dbname: str, modules: str):
    """Return a compact summary parsed from the resetdb log."""

    summary_lines = []

    if not log_path.exists():
        summary_lines.append(f"Log not found: {log_path}")
        return summary_lines

    try:
        lines = log_path.read_text(errors="ignore").splitlines()
        log_text = "\n".join(lines)
    except OSError as exc:
        summary_lines.append(f"Could not read log {log_path}: {exc}")
        return summary_lines

    summary_lines.append("=" * 80)
    summary_lines.append("DATABASE RESET SUMMARY")
    summary_lines.append("=" * 80)

    # Extract key information
    installed_modules = []
    errors = []
    warnings = []

    # Look for module installation messages
    for line in lines:
        # Module installed successfully
        if re.search(r"module.*installed|installing.*module", line, re.IGNORECASE):
            module_match = re.search(
                r"installing\s+['\"]?([a-z0-9_]+)", line, re.IGNORECASE
            )
            if module_match:
                mod_name = module_match.group(1)
                if mod_name not in installed_modules:
                    installed_modules.append(mod_name)

        # Errors
        if re.search(r"error|exception|failed|traceback", line, re.IGNORECASE):
            if "ERROR" in line.upper() or "Traceback" in line:
                # Extract meaningful error message
                error_match = re.search(
                    r"(?:ERROR|Error|Exception):\s*(.+?)(?:\s*$|\.)", line
                )
                if error_match:
                    error_msg = error_match.group(1).strip()
                    if len(error_msg) > 100:
                        error_msg = error_msg[:97] + "..."
                    if error_msg not in errors:
                        errors.append(error_msg)

        # Warnings
        if re.search(r"warning|warn", line, re.IGNORECASE):
            warn_match = re.search(r"(?:WARNING|Warning):\s*(.+?)(?:\s*$|\.)", line)
            if warn_match:
                warn_msg = warn_match.group(1).strip()
                if len(warn_msg) > 100:
                    warn_msg = warn_msg[:97] + "..."
                if warn_msg not in warnings:
                    warnings.append(warn_msg)

    # Database status
    db_dropped = "drop" in log_text.lower() or "dropped" in log_text.lower()
    db_created = (
        "create" in log_text.lower()
        or "created" in log_text.lower()
        or "init" in log_text.lower()
    )

    # Summary information
    summary_lines.append(f"Database: {dbname}")
    summary_lines.append(f"Modules: {modules}")
    summary_lines.append("")

    if db_dropped:
        summary_lines.append("✅ Database dropped successfully")
    if db_created:
        summary_lines.append("✅ Database created/initialized successfully")

    if installed_modules:
        summary_lines.append(f"\n📦 Installed modules ({len(installed_modules)}):")
        # Show first 20 modules, then count
        if len(installed_modules) <= 20:
            for mod in installed_modules[:20]:
                summary_lines.append(f"  • {mod}")
        else:
            for mod in installed_modules[:20]:
                summary_lines.append(f"  • {mod}")
            summary_lines.append(f"  ... and {len(installed_modules) - 20} more")

    if errors:
        summary_lines.append(f"\n❌ ERRORS ({len(errors)}):")
        for error in errors[:10]:  # Show first 10 errors
            summary_lines.append(f"  • {error}")
        if len(errors) > 10:
            summary_lines.append(f"  ... and {len(errors) - 10} more errors")

    if warnings:
        summary_lines.append(f"\n⚠️  WARNINGS ({len(warnings)}):")
        for warning in warnings[:5]:  # Show first 5 warnings
            summary_lines.append(f"  • {warning}")
        if len(warnings) > 5:
            summary_lines.append(f"  ... and {len(warnings) - 5} more warnings")

    # Check for success indicators
    if "stop-after-init" in log_text.lower() or "initdb" in log_text.lower():
        if not errors:
            summary_lines.append("\n✅ Database reset completed successfully!")
        else:
            summary_lines.append(
                "\n⚠️  Database reset completed with errors (see above)"
            )

    summary_lines.append("\n" + "=" * 80)
    summary_lines.append(f"Full log: {log_path}")
    summary_lines.append("=" * 80)

    return summary_lines


def _get_module_list(
    c,
    modules=None,
    core=False,
    extra=False,
    private=False,
    enterprise=False,
    only_installable=True,
):
    """Returns a list of addons according to the passed parameters.

    By default, refers to the addon from directory being worked on,
    unless other options are specified.
    """
    # Get list of dependencies for addon
    cmd = DOCKER_COMPOSE_CMD + " run --rm odoo addons list"
    if core:
        cmd += " --core"
    if extra:
        cmd += " --extra"
    if private:
        cmd += " --private"
    if enterprise:
        cmd += " --enterprise"
    if modules:
        cmd += f" -w {modules}"
    if only_installable:
        cmd += " --installable"
    with c.cd(str(PROJECT_ROOT)):
        module_list = c.run(
            cmd,
            env=UID_ENV,
            pty=False,
            hide="stdout",
        ).stdout.splitlines()[-1]
    return module_list


def _list_spp_modules():
    """Return sorted list of addon folder names starting with 'spp_'."""
    base = SRC_PATH / "openspp_modules"
    if not base.exists():
        return []
    modules = []
    for addon in base.iterdir():
        if (
            addon.is_dir()
            and addon.name.startswith("spp_")
            and (
                (addon / "__manifest__.py").is_file()
                or (addon / "__openerp__.py").is_file()
            )
        ):
            modules.append(addon.name)
    return sorted(modules)


def _spp_dependency_closure(modules_csv):
    """Return CSV of spp_* modules in the transitive deps of modules_csv."""
    if not modules_csv:
        return ""

    deps_csv = _expand_modules_with_deps(modules_csv)
    deps = [m.strip() for m in deps_csv.split(",") if m.strip()]
    spp_deps = sorted({m for m in deps if m.startswith("spp_")})
    return ",".join(spp_deps)


def _expand_modules_with_deps(modules_csv):
    """Return CSV of modules plus all their dependencies using manifestoo.

    Requires ``manifestoo`` CLI installed on the host (pip install manifestoo-core).
    Falls back to the original list if manifestoo is unavailable.
    """

    if not modules_csv:
        return modules_csv

    manifestoo_cmd = shutil.which("manifestoo")
    use_docker_manifestoo = False
    if not manifestoo_cmd:
        # Try the doodba odoo container, which ships with manifestoo
        use_docker_manifestoo = True

    addons_dirs = [
        SRC_PATH / "openspp_modules",
        SRC_PATH / "odoo" / "addons",
    ]

    addons_path = (
        "/opt/odoo/custom/src/openspp_modules,/opt/odoo/custom/src/odoo/addons"
        if use_docker_manifestoo
        else f"{addons_dirs[0]},{addons_dirs[1]}"
    )
    base_cmd = [
        "manifestoo",
        "--odoo-series",
        "19.0",
        "--addons-path",
        addons_path,
        "--select-include",
        modules_csv,
        "list-depends",
        "--transitive",
        "--include-selected",
        "--separator",
        ",",
        "--ignore-missing",
    ]

    if use_docker_manifestoo:
        cmd = [
            *DOCKER_COMPOSE_CMD.split(),
            "run",
            "--rm",
            "odoo",
            *base_cmd,
        ]
    else:
        cmd = [manifestoo_cmd, *base_cmd[1:]]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=UID_ENV if use_docker_manifestoo else None,
        )
        expanded = result.stdout.strip().splitlines()[-1]
        _logger.info("Expanded modules with dependencies: %s", expanded)
        return expanded
    except subprocess.CalledProcessError as exc:
        _logger.warning("manifestoo failed (%s); using original list", exc)
        return modules_csv


@task(
    help={
        "modules": "Comma-separated list of modules to test.",
        "core": "Test all core addons. Default: False",
        "extra": "Test all extra addons. Default: False",
        "private": "Test all private addons. Default: False",
        "enterprise": "Test all enterprise addons. Default: False",
        "skip": "Comma-separated list of modules to skip. Default: ''",
        "debugpy": "Whether or not to run tests in a VSCode debugging session. "
        "Default: False",
        "cur-file": "Path to the current file."
        " Addon name will be obtained from there to run tests",
        "mode": "Mode in which tests run. Options: ['init'(default), 'update']",
        "db_filter": "DB_FILTER regex to pass to the test container Set to ''"
        " to disable. Default: '^devel$'",
        "log_dir": "Directory (host) where log and xUnit files are written. Default: /tmp",
        "stream": "Stream full output to console instead of hiding it. Default: False",
    },
)
def test(
    c,
    modules=None,
    core=False,
    extra=False,
    private=False,
    enterprise=False,
    skip="",
    debugpy=False,
    cur_file=None,
    mode="init",
    db_filter="^devel$",
    with_deps=False,
    log_dir="/tmp",
    stream=False,
):
    """Run Odoo tests

    By default, tests addon from directory being worked on,
    unless other options are specified.

    NOTE: Odoo must be restarted manually after this to go back to normal mode
    """
    if not (modules or core or extra or private or enterprise):
        cur_module = _get_cwd_addon(cur_file or Path.cwd())
        if not cur_module:
            raise exceptions.ParseError(
                msg="Odoo addon to install not found. "
                "You must provide at least one option for modules"
                " or be in a subdirectory of one."
                " See --help for details."
            )
        modules = cur_module
    else:
        modules = _get_module_list(c, modules, core, extra, private, enterprise)
    odoo_command = ["odoo", "--test-enable", "--stop-after-init", "--workers=0"]
    if mode == "init":
        odoo_command.append("-i")
    elif mode == "update":
        odoo_command.append("-u")
    else:
        raise exceptions.ParseError(
            msg="Available modes are 'init' or 'update'. See --help for details."
        )
    # Skip test in some modules
    modules_list = modules.split(",")
    skip_list = [m for m in skip.split(",") if m]
    for m_to_skip in skip_list:
        if m_to_skip not in modules_list:
            # _logger.warning(
            #     "%s not found in the list of addons to test: %s", m_to_skip, modules
            # )
            continue
        modules_list.remove(m_to_skip)
    modules = ",".join(modules_list)
    if with_deps:
        modules = _expand_modules_with_deps(modules)
        modules_list = modules.split(",") if modules else []
        # Re-apply skips after dependency expansion (to avoid running tests from
        # modules pulled in as dependencies like queue_job).
        modules_list = [m for m in modules_list if m not in skip_list]
        modules = ",".join(modules_list)

    # Determine log file path early and display it
    log_dir_path = Path(log_dir).expanduser().resolve()
    log_dir_path.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = _sanitize_filename(modules)
    log_path = log_dir_path / f"{safe_name}-{ts}.log"
    print(f"Test log file: {log_path}")

    odoo_command.append(modules)
    if ODOO_VERSION >= 12:
        # Limit tests to explicit list
        # Filter spec format (comma-separated)
        # [-][tag][/module][:class][.method]
        odoo_command.extend(["--test-tags", f"/{',/'.join(modules_list)}"])
    if debugpy:
        _test_in_debug_mode(c, odoo_command)
    else:
        cmd = [DOCKER_COMPOSE_CMD, "run", "--rm"]
        if db_filter:
            cmd.extend(["-e", f"DB_FILTER='{db_filter}'"])
        # Share log directory with host so artifacts persist
        log_dir = str(log_dir_path)
        cmd.extend(["-v", f"{log_dir}:{log_dir}"])
        cmd.append("odoo")
        cmd.extend(odoo_command)
        with c.cd(str(PROJECT_ROOT)):
            if stream:
                # Stream output to console in real-time, also save to log using tee
                print(f"Streaming output (also saved to: {log_path})")
                print("-" * 80)
                # Use tee to both display and save output
                run_cmd = (
                    'bash -o pipefail -c "' + " ".join(cmd) + f' 2>&1 | tee {log_path}"'
                )
                result = c.run(run_cmd, env=UID_ENV, pty=True, warn=True)
            else:
                # Hide output and save to log file
                run_cmd = (
                    'bash -o pipefail -c "' + " ".join(cmd) + f' > {log_path} 2>&1"'
                )
                result = c.run(run_cmd, env=UID_ENV, pty=False, warn=True, hide=True)

        # Display summary
        if log_path.exists():
            print()  # Add blank line before summary
            try:
                summary_lines = _summarize_test_results(log_path)
                for line in summary_lines:
                    print(line)
            except Exception as exc:
                print(f"Failed to generate test summary: {exc}")
                print(f"Full log: {log_path}")
        else:
            print(f"\nNote: Log file not found at {log_path}")

        if result.failed:
            raise exceptions.Exit(code=result.exited)


@task(
    help={
        "skip": "Comma-separated list of modules to skip. Default: 'queue_job'.",
        "with_deps": "Expand modules with dependencies before running. Default: True.",
        "mode": "Mode in which tests run. Options: ['init'(default), 'update']",
        "db_filter": "DB_FILTER regex to pass to the test container. Default: '^devel$'",
        "debugpy": "Run tests with debugpy enabled. Default: False",
        "log_dir": "Directory (host) where log and xUnit files are written. Default: /tmp",
        "stream": "Stream full output to console instead of hiding it. Default: False",
    }
)
def test_spp(
    c,
    skip="queue_job",
    with_deps=True,
    mode="init",
    db_filter="^devel$",
    debugpy=False,
    log_dir="/tmp",
    stream=False,
):
    """Run tests for all spp_* addons found in openspp_modules."""
    modules_list = _list_spp_modules()
    if not modules_list:
        raise exceptions.ParseError(
            msg="No spp_* addons found under odoo/custom/src/openspp_modules"
        )
    modules = ",".join(modules_list)
    _logger.info("Testing spp addons: %s", modules)
    test(
        c,
        modules=modules,
        skip=skip,
        with_deps=with_deps,
        mode=mode,
        db_filter=db_filter,
        debugpy=debugpy,
        log_dir=log_dir,
        stream=stream,
    )


@task(
    help={
        "modules": "Comma-separated root modules to analyze. Default: 'spp_mis_demo'.",
        "skip": "Comma-separated list of modules to skip. Default: 'queue_job'.",
        "mode": "Mode in which tests run. Options: ['init'(default), 'update']",
        "db_filter": "DB_FILTER regex to pass to the test container. Default: '^devel$'",
        "debugpy": "Run tests with debugpy enabled. Default: False",
        "log_dir": "Directory (host) where log and xUnit files are written. Default: /tmp",
        "stream": "Stream full output to console instead of hiding it. Default: False",
    }
)
def test_spp_deps(
    c,
    modules="spp_mis_demo",
    skip="queue_job",
    mode="init",
    db_filter="^devel$",
    debugpy=False,
    log_dir="/tmp",
    stream=False,
):
    """Run tests for spp_* modules in the dependency closure of given modules."""
    spp_modules_csv = _spp_dependency_closure(modules)
    if not spp_modules_csv:
        raise exceptions.ParseError(
            msg=f"No spp_* dependencies found for modules: {modules}"
        )
    # Safety: enforce spp_* filter even if closure contains extra deps
    modules_list = [m for m in spp_modules_csv.split(",") if m.startswith("spp_")]
    modules_list = sorted(set(modules_list))
    modules_csv = ",".join(modules_list)
    _logger.info("Testing spp dependency addons: %s", modules_csv)
    test(
        c,
        modules=modules_csv,
        skip=skip,
        with_deps=False,
        mode=mode,
        db_filter=db_filter,
        debugpy=debugpy,
        log_dir=log_dir,
        stream=stream,
    )


@task(
    help={"purge": "Remove all related containers, networks images and volumes"},
)
def stop(c, purge=False):
    """Stop and (optionally) purge environment."""
    cmd = f"{DOCKER_COMPOSE_CMD} down --remove-orphans"
    if purge:
        cmd += " --rmi local --volumes"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, pty=True)


@task(
    help={
        "dbname": "The DB that will be DESTROYED and recreated. Default: 'devel'.",
        "modules": "Comma-separated list of modules to install. Default: 'base'.",
        "core": "Install all core addons. Default: False",
        "extra": "Install all extra addons. Default: False",
        "private": "Install all private addons. Default: False",
        "enterprise": "Install all enterprise addons. Default: False",
        "populate": "Run preparedb task right after (only available for v11+)."
        " Default: True",
        "dependencies": "Install only the dependencies of the specified addons."
        "Default: False",
        "log_dir": "Directory (host) where log files are written. Default: /tmp",
        "stream": "Stream full output to console instead of hiding it. Default: False",
    },
)
def resetdb(
    c,
    modules=None,
    core=False,
    extra=False,
    private=False,
    enterprise=False,
    dbname="devel",
    populate=True,
    dependencies=False,
    log_dir="/tmp",
    stream=False,
):
    """Reset the specified database with the specified modules.

    Uses click-odoo-initdb behind the scenes, which has a caching system that
    makes DB resets quicker. See its docs for more info.
    """
    if dependencies:
        modules = _get_module_dependencies(c, modules, core, extra, private, enterprise)
    elif core or extra or private or enterprise:
        modules = _get_module_list(c, modules, core, extra, private, enterprise)
    else:
        modules = modules or "base"

    # Set up log file
    log_dir_path = Path(log_dir).expanduser().resolve()
    log_dir_path.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = _sanitize_filename(f"{dbname}-{modules}")
    log_path = log_dir_path / f"resetdb-{safe_name}-{ts}.log"
    print(f"ResetDB log file: {log_path}")

    modules_display = modules
    with c.cd(str(PROJECT_ROOT)):
        # Stop odoo
        if stream:
            print("Stopping Odoo...")
        c.run(f"{DOCKER_COMPOSE_CMD} stop odoo", pty=stream)

        _run = f"{DOCKER_COMPOSE_CMD} run --rm -l traefik.enable=false odoo"

        # Drop database
        if stream:
            print(f"Dropping database '{dbname}'...")
            print("-" * 80)

        drop_cmd = f"{_run} click-odoo-dropdb {dbname}"
        if stream:
            c.run(
                f"{drop_cmd} 2>&1 | tee -a {log_path}",
                env=UID_ENV,
                warn=True,
                pty=True,
            )
        else:
            c.run(
                f"{drop_cmd} >> {log_path} 2>&1",
                env=UID_ENV,
                warn=True,
                pty=False,
                hide=True,
            )

        # Create/initialize database
        lang = os.getenv("INITIAL_LANG")
        lang_opt = f" --lang {lang}" if lang else ""

        if stream:
            print(f"\nInitializing database '{dbname}' with modules: {modules}...")
            print("-" * 80)

        if ODOO_VERSION >= 19:
            # Odoo 19: Registry.new(force_demo=...) removed → avoid click-odoo-initdb
            # Use native Odoo CLI; --without-demo=all replaces force_demo=False
            lang_opt19 = f" --load-language={lang}" if lang else ""
            init_cmd = (
                f"{_run} odoo --stop-after-init -d {dbname} -i {modules}"
                f"{lang_opt19} --without-demo=all"
            )
        else:
            # Older versions keep using click-odoo-initdb
            init_cmd = f"{_run} click-odoo-initdb -n {dbname} -m {modules}{lang_opt}"

        if stream:
            c.run(
                f"{init_cmd} 2>&1 | tee -a {log_path}",
                env=UID_ENV,
                pty=True,
            )
        else:
            c.run(
                f"{init_cmd} >> {log_path} 2>&1",
                env=UID_ENV,
                pty=False,
                hide=True,
            )

    # Display summary
    if log_path.exists():
        print()  # Add blank line before summary
        try:
            summary_lines = _summarize_resetdb_results(
                log_path, dbname, modules_display
            )
            for line in summary_lines:
                print(line)
        except Exception as exc:
            print(f"Failed to generate resetdb summary: {exc}")
            print(f"Full log: {log_path}")

    if populate and ODOO_VERSION < 11:
        _logger.warn(
            f"Skipping populate task as it is not available in v{ODOO_VERSION}"
        )
        populate = False
    if populate:
        preparedb(c)


@task()
def preparedb(c):
    """Run the `preparedb` script inside the container

    Populates the DB with some helpful config
    """
    if ODOO_VERSION < 11:
        raise exceptions.PlatformError(
            "The preparedb script is not available for Doodba environments bellow v11."
        )
    with c.cd(str(PROJECT_ROOT)):
        c.run(
            f"{DOCKER_COMPOSE_CMD} run --rm -l traefik.enable=false odoo preparedb",
            env=UID_ENV,
            pty=True,
        )


@task()
def restart(c, quick=True):
    """Restart odoo container(s)."""
    cmd = f"{DOCKER_COMPOSE_CMD} restart"
    if quick:
        cmd = f"{cmd} -t0"
    cmd = f"{cmd} odoo odoo_proxy"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, env=UID_ENV, pty=True)


@task(
    help={
        "container": "Names of the containers from which logs will be obtained."
        " You can specify a single one, or several comma-separated names."
        " Default: None (show logs for all containers)"
    },
)
def logs(c, tail=10, follow=True, container=None):
    """Obtain last logs of current environment."""
    cmd = f"{DOCKER_COMPOSE_CMD} logs"
    if follow:
        cmd += " -f"
    if tail:
        cmd += f" --tail {tail}"
    if container:
        cmd += f" {container.replace(',', ' ')}"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, pty=True)


@task
def after_update(c):
    """Execute some actions after a copier update or init"""
    # Make custom build scripts executable
    if ODOO_VERSION < 11:
        files = (
            Path(PROJECT_ROOT, "odoo", "custom", "build.d", "20-update-pg-repos"),
            Path(PROJECT_ROOT, "odoo", "custom", "build.d", "10-fix-certs"),
        )
        for script_file in files:
            # Ignore if, for some reason, the file didn't end up in the generated
            # project despite of the correct version (e.g. Copier exclusions)
            if not script_file.exists():
                continue
            cur_stat = script_file.stat()
            # Like chmod ug+x
            script_file.chmod(cur_stat.st_mode | stat.S_IXUSR | stat.S_IXGRP)
    else:
        # Remove version-specific build scripts if the copier update didn't
        # HACK: https://github.com/copier-org/copier/issues/461
        files = (
            Path(PROJECT_ROOT, "odoo", "custom", "build.d", "20-update-pg-repos"),
            Path(PROJECT_ROOT, "odoo", "custom", "build.d", "10-fix-certs"),
        )
        for script_file in files:
            # missing_ok argument would take care of this, but it was only added for
            # Python 3.8
            if script_file.exists():
                script_file.unlink()


@task(
    help={
        "source_db": "The source DB name. Default: 'devel'.",
        "destination_db": (
            "The destination DB name. Default: '[SOURCE_DB_NAME]-[CURRENT_DATE]'"
        ),
    },
)
def snapshot(
    c,
    source_db="devel",
    destination_db=None,
):
    """Snapshot current database and filestore.

    Uses click-odoo-copydb behind the scenes to make a snapshot.
    """
    if not destination_db:
        destination_db = f"{source_db}-{datetime.now().strftime('%Y_%m_%d-%H_%M')}"
    with c.cd(str(PROJECT_ROOT)):
        cur_state = c.run(f"{DOCKER_COMPOSE_CMD} stop odoo db", pty=True).stdout
        _logger.info("Snapshoting current %s DB to %s", (source_db, destination_db))
        _run = f"{DOCKER_COMPOSE_CMD} run --rm -l traefik.enable=false odoo"
        c.run(
            f"{_run} click-odoo-copydb {source_db} {destination_db}",
            env=UID_ENV,
            pty=True,
        )
        if "Stopping" in cur_state:
            # Restart services if they were previously active
            c.run(f"{DOCKER_COMPOSE_CMD} start odoo db", pty=True)


@task(
    help={
        "snapshot_name": "The snapshot name. If not provided,"
        "the script will try to find the last snapshot"
        " that starts with the destination_db name",
        "destination_db": "The destination DB name. Default: 'devel'",
    },
)
def restore_snapshot(
    c,
    snapshot_name=None,
    destination_db="devel",
):
    """Restore database and filestore snapshot.

    Uses click-odoo-copydb behind the scenes to restore a DB snapshot.
    """
    with c.cd(str(PROJECT_ROOT)):
        cur_state = c.run(f"{DOCKER_COMPOSE_CMD} stop odoo db", pty=True).stdout
        if not snapshot_name:
            # List DBs
            res = c.run(
                f"{DOCKER_COMPOSE_CMD} run --rm -e LOG_LEVEL=WARNING odoo psql -tc"
                " 'SELECT datname FROM pg_database;'",
                env=UID_ENV,
                hide="stdout",
            )
            db_list = []
            for db in res.stdout.splitlines():
                # Parse and filter DB List
                if not db.lstrip().startswith(destination_db):
                    continue
                db_name = db.lstrip()
                try:
                    db_date = datetime.strptime(
                        db_name.lstrip(f"{destination_db}-"), "%Y_%m_%d-%H_%M"
                    )
                    db_list.append((db_name, db_date))
                except ValueError:
                    continue
            snapshot_name = max(db_list, key=lambda x: x[1])[0]
            if not snapshot_name:
                raise exceptions.PlatformError(
                    "No snapshot found for destination_db %s" % destination_db  # noqa: UP031
                )
        _logger.info("Restoring snapshot %s to %s", (snapshot_name, destination_db))
        _run = f"{DOCKER_COMPOSE_CMD} run --rm -l traefik.enable=false odoo"
        c.run(
            f"{_run} click-odoo-dropdb {destination_db}",
            env=UID_ENV,
            warn=True,
            pty=True,
        )
        c.run(
            f"{_run} click-odoo-copydb {snapshot_name} {destination_db}",
            env=UID_ENV,
            pty=True,
        )
        if "Stopping" in cur_state:
            c.run(f"{DOCKER_COMPOSE_CMD} start odoo db", pty=True)


@task(
    help={},
)
def update(c):
    """Migrate Odoo addons"""
    cmd = (
        DOCKER_COMPOSE_CMD
        + " run --rm odoo click-odoo-update --watcher-max-seconds 600"
    )
    with c.cd(str(PROJECT_ROOT)):
        c.run(
            cmd,
            env=UID_ENV,
            pty=True,
        )


@task(
    help={
        "addons_dir": "Directory or specific folder path containing the addons to update POT files for. Default: odoo/custom/src",
        "commit": "Whether to commit changes. Default: False",
        "modules": "Comma-separated list of module patterns to match (e.g., 'spp_*,g2p_*'). Default: None (all modules)",
        "database": "Database name to use. Default: devel",
        "no_fuzzy": "Disable fuzzy matching. Default: False",
        "update_po": "Update .po files after generating .pot files. Default: False",
        "lang": "Language code for PO files to update/create. Default: None",
        "force": "Force update existing PO files. Default: False",
        "msgmerge": "Run msgmerge if POT file is created/updated. Default: False",
        "create_i18n": "Create i18n directories if they don't exist. Default: True",
        "debug": "Run in debug mode with extra logging. Default: False",
    }
)
def update_pot(
    c,
    addons_dir="odoo/custom/src",
    commit=False,
    modules=None,
    database="devel",
    no_fuzzy=False,
    update_po=False,
    lang=None,
    force=False,
    msgmerge=False,
    create_i18n=True,
    debug=False,
):
    """Update POT files and optionally PO files for all addons in specified directory or specific folder.

    IMPORTANT: Modules must be installed in the database before translations can be extracted.
    The extraction process uses Odoo's translation export mechanism which requires installed modules.

    Examples:
        1. Extract POT files for specific module patterns:
           invoke update-pot --modules="spp_*,g2p_*,pds_*" --database=devel --no-fuzzy --msgmerge

        2. Update POT files and all existing PO files:
           invoke update-pot --modules="spp_*,g2p_*,pds_*" --database=devel --no-fuzzy --update-po

        3. Create/update a specific language PO file:
           invoke update-pot --modules="spp_*,g2p_*,pds_*" --update-po --lang=fr

        4. Force update existing PO files:
           invoke update-pot --modules="spp_*,g2p_*,pds_*" --update-po --lang=fr --force

        5. Process a specific module directory:
           invoke update-pot --addons-dir="odoo/custom/src/openspp_modules" --database=devel --no-fuzzy

        6. Run with debug mode for more logging:
           invoke update-pot --addons-dir="odoo/custom/src/openspp_modules" --debug
    """
    module_list = []
    auto_addons = Path(PROJECT_ROOT, "odoo", "auto", "addons")
    addons_path = None

    if debug:
        _logger.info("Running in debug mode")
        _logger.info("PROJECT_ROOT: %s", PROJECT_ROOT)
        _logger.info("auto_addons path: %s", auto_addons)

    if modules:
        # Module pattern specified - use this directly
        # Handle comma-separated patterns
        patterns = [p.strip() for p in modules.split(",")]
        for pattern in patterns:
            if "*" in pattern:
                # For patterns like spp_*, g2p_*, etc.
                matching = list(auto_addons.glob(pattern))
                module_list.extend([m.name for m in matching if m.is_dir()])
            else:
                # For exact module names
                if (auto_addons / pattern).is_dir():
                    module_list.append(pattern)
    else:
        # No module pattern - look at specified directory
        addons_path = Path(PROJECT_ROOT, addons_dir)
        if not addons_path.exists():
            # Try as absolute path
            addons_path = Path(addons_dir)
            if not addons_path.exists():
                raise exceptions.ParseError("Path %s does not exist" % addons_dir)

        if debug:
            _logger.info("addons_path: %s", addons_path)

        # Figure out if we need to find modules in a repository or in a specific folder
        if addons_path.name in ("src", "addons"):
            # Path is a general container, find all repositories
            for repo_path in addons_path.glob("*"):
                if not repo_path.is_dir() or repo_path.name == "odoo":
                    continue

                # Try to get a list of modules in this repo from auto/addons
                repo_name = repo_path.name
                if debug:
                    _logger.info("Looking for modules in repo: %s", repo_name)

                for module_dir in auto_addons.glob("*"):
                    if not module_dir.is_dir():
                        continue

                    # Check if this module belongs to the repository
                    # We do this by checking if a link exists in auto/addons pointing to the repo
                    if module_dir.is_symlink():
                        try:
                            target = module_dir.resolve()
                            if debug:
                                _logger.info(
                                    "Module %s is a symlink to %s",
                                    module_dir.name,
                                    target,
                                )
                            if repo_name in str(target):
                                module_list.append(module_dir.name)
                        except Exception as e:
                            _logger.warning(
                                "Error resolving symlink for %s: %s", module_dir, e
                            )

        else:
            # Path is a specific repo or module, figure out which
            repo_or_module_name = addons_path.name

            if debug:
                _logger.info("Examining specific path: %s", repo_or_module_name)

            # First check if it's a specific module
            if (addons_path / "__manifest__.py").exists() or (
                addons_path / "__openerp__.py"
            ).exists():
                # It's a module - find its name in auto/addons
                module_name = repo_or_module_name
                if (auto_addons / module_name).exists():
                    module_list.append(module_name)
                    if debug:
                        _logger.info("Found module %s in auto/addons", module_name)
            else:
                # It's a repository - find all modules that belong to this repo
                repo_name = repo_or_module_name

                if debug:
                    _logger.info("Looking for modules in repository: %s", repo_name)

                # Check the repository structure to determine the module path pattern
                if (addons_path / "addons").is_dir():
                    # Modules are in an 'addons' subdirectory
                    module_dirs = list(addons_path.glob("addons/*"))
                    if debug:
                        _logger.info(
                            "Found addons subdirectory with %d potential modules",
                            len(module_dirs),
                        )
                else:
                    # Modules are directly in the repository
                    module_dirs = list(addons_path.glob("*"))
                    if debug:
                        _logger.info(
                            "Found %d potential modules directly in repo",
                            len(module_dirs),
                        )

                # Find corresponding modules in auto/addons
                for module_dir in module_dirs:
                    if not module_dir.is_dir():
                        continue

                    module_name = module_dir.name
                    if (module_dir / "__manifest__.py").exists() or (
                        module_dir / "__openerp__.py"
                    ).exists():
                        if (auto_addons / module_name).exists():
                            module_list.append(module_name)
                            if debug:
                                _logger.info(
                                    "Found module %s in auto/addons", module_name
                                )

    if not module_list:
        # If we still don't have modules, let's try a direct path lookup from auto/addons
        if addons_path:
            _logger.info(
                "No modules found using standard methods, trying direct path mapping..."
            )
            for module_dir in auto_addons.glob("*"):
                if not module_dir.is_dir():
                    continue

                if module_dir.is_symlink():
                    try:
                        target = os.path.normpath(str(module_dir.resolve()))
                        src_path = os.path.normpath(str(addons_path))

                        if debug:
                            _logger.info(
                                "Module %s target: %s", module_dir.name, target
                            )
                            _logger.info("Comparing with source path: %s", src_path)

                        if src_path in target or str(addons_path.name) in target:
                            module_list.append(module_dir.name)
                            if debug:
                                _logger.info(
                                    "Matched module %s by path", module_dir.name
                                )
                    except Exception as e:
                        _logger.warning(
                            "Error resolving symlink for %s: %s", module_dir, e
                        )

        if not module_list:
            # Last attempt: try to find any modules with repository name in their paths
            if addons_path:
                repo_name = addons_path.name
                if debug:
                    _logger.info("Trying to match modules by repo name: %s", repo_name)
                for module_dir in auto_addons.glob("*"):
                    if not module_dir.is_dir():
                        continue

                    if module_dir.is_symlink():
                        try:
                            target = str(module_dir.resolve())
                            if repo_name in target:
                                module_list.append(module_dir.name)
                                if debug:
                                    _logger.info(
                                        "Matched module %s by repo name in path",
                                        module_dir.name,
                                    )
                        except Exception as e:
                            _logger.warning(
                                "Error resolving symlink for %s: %s", module_dir, e
                            )

    if not module_list:
        raise exceptions.ParseError(
            "No modules found to process in %s. Try using --modules instead."
            % addons_dir
        )

    _logger.info("Processing modules: %s", ", ".join(module_list))

    # Create i18n directories if needed
    if create_i18n:
        for module_name in module_list:
            module_path = auto_addons / module_name
            i18n_dir = module_path / "i18n"
            if not i18n_dir.exists():
                _logger.info("Creating i18n directory for %s", module_name)
                i18n_dir.mkdir(parents=True, exist_ok=True)

    # Build the command following the pattern used by other tasks
    cmd = (
        "%s run --rm odoo click-odoo-makepot --addons-dir /opt/odoo/auto/addons"
        % DOCKER_COMPOSE_CMD
    )

    if database:
        cmd += " -d %s" % database
    if no_fuzzy:
        cmd += " --no-fuzzy-matching"
    if commit:
        cmd += " --commit"
    if msgmerge:
        cmd += " --msgmerge"

    # Add modules list
    cmd += " -m %s" % ",".join(module_list)

    # Add log level for more verbose output if debugging
    if debug:
        cmd += " --log-level=debug"

    with c.cd(str(PROJECT_ROOT)):
        try:
            _logger.info("Running command: %s", cmd)
            c.run(cmd, env=UID_ENV, pty=True)
            _logger.info("POT file generation completed")
        except Exception as e:
            _logger.error("Failed to update POT files: %s", e)
            return

    # Check for created POT files and report
    pot_files_created = []
    for module_name in module_list:
        module_path = auto_addons / module_name
        i18n_dir = module_path / "i18n"
        pot_files = list(i18n_dir.glob("*.pot"))
        if pot_files:
            pot_files_created.extend([str(p) for p in pot_files])

    if pot_files_created:
        _logger.info("POT files created/updated: %d", len(pot_files_created))
    else:
        _logger.warning("No POT files were created or updated. This might be because:")
        _logger.warning("1. The modules have no translatable strings")
        _logger.warning("2. The POT files already exist and were not changed")
        _logger.warning("3. The modules are not installed in the database")
        _logger.warning("4. There was an issue with the extraction process")

    # Update PO files if requested
    if update_po:
        _logger.info("Updating PO files")
        po_files_updated = 0
        # Find all i18n directories in the processed modules
        i18n_dirs = []
        for module in module_list:
            module_path = auto_addons / module
            if (module_path / "i18n").exists():
                i18n_dirs.append(module_path / "i18n")

        for i18n_dir in i18n_dirs:
            # Find all POT files
            pot_files = list(i18n_dir.glob("*.pot"))
            for pot_file in pot_files:
                if lang:
                    # Update/create specific language PO file
                    po_file = pot_file.parent / f"{lang}.po"
                    if force or not po_file.exists():
                        if not po_file.exists():
                            # Create new PO file
                            _logger.info("Creating new PO file %s", po_file)
                            cmd = "msginit --no-translator -l %s -i %s -o %s" % (
                                lang,
                                pot_file,
                                po_file,
                            )
                        else:
                            # Update existing PO file
                            _logger.info("Updating existing PO file %s", po_file)
                            cmd = "msgmerge --no-fuzzy-matching -N -U %s %s" % (
                                po_file,
                                pot_file,
                            )
                        with c.cd(str(PROJECT_ROOT)):
                            try:
                                c.run(cmd, hide=True)
                                _logger.info("Updated %s", po_file)
                                po_files_updated += 1
                            except Exception as e:
                                _logger.error("Failed to update %s: %s", po_file, e)
                else:
                    # Update all existing PO files
                    po_files = list(i18n_dir.glob("*.po"))
                    if not po_files:
                        _logger.info("No existing PO files found in %s", i18n_dir)
                    for po_file in po_files:
                        _logger.info("Updating PO file %s", po_file)
                        cmd = "msgmerge --update %s %s" % (po_file, pot_file)
                        with c.cd(str(PROJECT_ROOT)):
                            try:
                                c.run(cmd, hide=True)
                                _logger.info("Updated %s", po_file)
                                po_files_updated += 1
                            except Exception as e:
                                _logger.error("Failed to update %s: %s", po_file, e)

        _logger.info("Total PO files updated: %d", po_files_updated)


def _wait_for_odoo_ready(c, url="http://odoo:8069/web/login", max_attempts=60, delay=2):
    """Wait for Odoo to be ready and accessible.

    Args:
        c: Invoke context
        url: URL to check (default: http://odoo:8069/web/login)
        max_attempts: Maximum number of attempts (default: 60)
        delay: Delay between attempts in seconds (default: 2)

    Raises:
        exceptions.Exit: If Odoo is not ready after max_attempts
    """
    _logger.info("Waiting for Odoo to be ready at %s...", url)

    # First, wait a bit for containers to start
    time.sleep(2)

    # Check from within the e2e-runner container since that's where tests run
    # and where the network connectivity matches the test environment
    for attempt in range(1, max_attempts + 1):
        try:
            # Use curl from within the e2e-runner container to check Odoo
            # Use -T flag for non-interactive mode and handle errors gracefully
            cmd = (
                f"{E2E_COMPOSE_FILES} exec -T e2e-runner "
                f'sh -c \'curl -f -s -o /dev/null -w "%{{http_code}}" {url} 2>/dev/null || echo "000"\''
            )
            result = c.run(cmd, hide=True, warn=True)

            # Check if we got a successful HTTP response (2xx or 3xx)
            http_code = result.stdout.strip() if result.stdout else "000"
            if http_code.startswith(("2", "3")):
                _logger.info("Odoo is ready! (HTTP %s)", http_code)
                return

            if attempt % 5 == 0:
                _logger.info(
                    "Still waiting for Odoo... (attempt %d/%d, last code: %s)",
                    attempt,
                    max_attempts,
                    http_code,
                )
        except Exception as e:
            if attempt % 5 == 0:
                _logger.debug("Error checking Odoo readiness: %s", e)

        time.sleep(delay)

    raise exceptions.Exit(
        code=1,
        message=f"Odoo did not become ready after {max_attempts} attempts "
        f"(checked {url})",
    )


@task(
    help={
        "services": "Services to start (default: odoo odoo_proxy e2e-runner).",
        "wait": "Wait for Odoo to be ready before returning. Default: True",
    },
)
def e2e_up(c, services="odoo odoo_proxy e2e-runner", wait=True):
    """Start Odoo + Playwright e2e runner services."""
    cmd = f"{E2E_COMPOSE_FILES} up -d {services}"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, env=UID_ENV, pty=True)

    if wait and "odoo" in services:
        _wait_for_odoo_ready(c)


@task
def e2e_install(c):
    """Install E2E dependencies inside the runner container."""
    cmd = f"{E2E_COMPOSE_FILES} exec e2e-runner npm install"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, pty=True)


@task(
    help={
        "project": "Playwright project to run (smoke, setup, spp-mis-demo-v2, all, firefox).",
        "headed": "Run browser in headed mode.",
        "debug": "Enable Playwright debug mode.",
    },
)
def e2e(c, project="smoke", headed=False, debug=False):
    """Run Playwright tests in the runner container."""
    flags = []
    if headed:
        flags.append("--headed")
    if debug:
        flags.append("--debug")
    flags_str = " ".join(flags)
    cmd = (
        f"{E2E_COMPOSE_FILES} exec e2e-runner "
        f"npx playwright test --project={project} {flags_str}"
    ).strip()
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, pty=True)


@task
def e2e_report(c):
    """Open the last Playwright HTML report."""
    cmd = f"{E2E_COMPOSE_FILES} exec e2e-runner npx playwright show-report reports/html"
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, pty=True)


def _run_odoo_shell(c, dbname, py_command, stream=False):
    """Execute a small python command inside odoo shell for the given DB."""
    cmd = (
        f"{DOCKER_COMPOSE_CMD} run --rm -l traefik.enable=false "
        f'-e LOG_LEVEL=INFO odoo bash -c "echo \\"{py_command}\\" | '
        f'odoo shell -d {dbname} --no-http --stop-after-init"'
    )
    with c.cd(str(PROJECT_ROOT)):
        c.run(cmd, pty=stream)


def _generate_demo_data(c, profile_key, dbname, stream=False):
    """Generate demo data for a given profile after modules are installed."""
    profile = DEMO_PROFILES.get(profile_key)
    if not profile:
        return

    demo_kinds = profile.get("generate_demo") or []
    if isinstance(demo_kinds, str):
        demo_kinds = [demo_kinds]

    # Note: Security groups are now assigned automatically by the demo generator
    # when change requests are created (see spp_mis_demo_v2/models/mis_demo_generator.py)

    for demo_kind in demo_kinds:
        if demo_kind == "mis_demo_v2":
            # Use the MIS demo generator model to populate full storyline data
            # Note: action_generate() now commits internally, but we add explicit commit for shell safety
            py_cmd = (
                "gen = env['spp.mis.demo.generator'].create({'name': 'E2E Demo', 'create_change_requests': True}); "
                "gen.action_generate(); "
                "env.cr.commit()"
            )
            _logger.info("Generating MIS demo data on DB %s", dbname)
            _run_odoo_shell(c, dbname, py_cmd, stream=stream)
        elif demo_kind == "grm_demo":
            # GRM demo tickets (requires registrants/programs)
            py_cmd = (
                "env['spp.grm.demo.generator']"
                ".create({'name': 'GRM Demo Data'}).generate_tickets()"
            )
            _logger.info("Generating GRM demo data on DB %s", dbname)
            _run_odoo_shell(c, dbname, py_cmd, stream=stream)
        elif demo_kind == "case_demo":
            # Case management demo data
            py_cmd = (
                "env['spp.case.demo.generator']"
                ".create({'name': 'Case Demo Data'}).generate_cases()"
            )
            _logger.info("Generating Case Management demo data on DB %s", dbname)
            _run_odoo_shell(c, dbname, py_cmd, stream=stream)
        else:
            _logger.warning("Unknown demo generator '%s' skipped", demo_kind)


def _assign_test_groups(c, dbname, stream=False):
    """Assign necessary security groups to admin user for E2E tests.

    NOTE: This function is deprecated. Security groups are now assigned automatically
    by demo generators (e.g., spp_mis_demo_v2 assigns CR groups when creating change requests).
    This function is kept for backward compatibility but may not be called.
    """
    py_cmd = (
        "from odoo import Command; "
        "admin = env['res.users'].search([('login', '=', 'admin')], limit=1); "
        "groups_to_assign = ["
        "    'spp_change_request_v2.group_cr_validator',"  # Validator can see all CRs (not just own)
        "    'spp_case_base.group_case_manager',"  # Manager can see all cases (not just assigned)
        "    'spp_grm.group_grm_manager',"  # Manager can see all tickets (not just assigned)
        "]; "
        "for group_xmlid in groups_to_assign: "
        "    try: "
        "        group_id = env.ref(group_xmlid, raise_if_not_found=False); "
        "        if group_id and group_id.id not in admin.group_ids.ids: "
        "            admin.write({'group_ids': [Command.link(group_id.id)]}); "
        "    except: "
        "        pass; "
        "env.cr.commit()"
    )
    _logger.info("Assigning test security groups to admin user on DB %s", dbname)
    _run_odoo_shell(c, dbname, py_cmd, stream=stream)


def _get_demo_profile(demo_key):
    """Return a normalized demo profile."""
    key = demo_key.strip().lower().replace("_", "-")
    if key not in DEMO_PROFILES:
        raise exceptions.ParseError(
            msg=f"Unknown demo '{demo_key}'. Available: {', '.join(sorted(DEMO_PROFILES))}"
        )
    return DEMO_PROFILES[key]


@task(
    help={
        "demo": f"Demo profile to run. Available: {', '.join(sorted(DEMO_PROFILES))}. Default: mis-demo-v2",
        "dbname": "Database name to reset and test against. Default: devel",
        "headed": "Run Playwright in headed mode.",
        "debug": "Enable Playwright debug mode.",
        "no_reset": "Skip DB reset/installation step.",
        "modules": "Override modules CSV to install instead of the profile default.",
        "project": "Override Playwright project name instead of the profile default.",
        "populate": "Run preparedb after reset (default False).",
        "stream": "Stream resetdb output to console. Default: True.",
        "install_deps": "Run npm install inside e2e-runner before tests. Default: True.",
        "no_demo": "Skip demo data generation even if profile defines it.",
    }
)
def e2e_demo(
    c,
    demo="mis-demo-v2",
    dbname="devel",
    headed=False,
    debug=False,
    no_reset=False,
    modules=None,
    project=None,
    populate=False,
    stream=True,
    install_deps=True,
    no_demo=False,
):
    """Reset DB, install modules, generate demo data, then run its matching Playwright suite.

    Demo data generation is automatic based on the demo profile unless --no-demo is passed.
    """

    profile = _get_demo_profile(demo)
    modules_csv = modules or profile["modules"]
    project_name = project or profile["project"]

    if not no_reset:
        resetdb(
            c,
            modules=modules_csv,
            dbname=dbname,
            populate=populate,
            stream=stream,
        )

    if not no_demo and profile.get("generate_demo"):
        _generate_demo_data(c, demo, dbname, stream=stream)

    # Ensure services and runner are up
    e2e_up(c)
    if install_deps:
        e2e_install(c)

    e2e(c, project=project_name, headed=headed, debug=debug)


@task(
    help={
        "module_name": "Name of the module to scaffold.",
        "path": "Path where to create the module. Default: current directory.",
    },
)
def scaffold(
    c,
    module_name,
    path=None,
):
    """Scaffold a new Odoo module.

    Creates a new Odoo module with the basic structure using odoo scaffold command.
    """
    if not module_name:
        raise exceptions.ParseError(
            msg="Module name is required. See --help for details."
        )

    # Use current directory if no path specified, otherwise use the specified path
    target_path = path or str(Path.cwd())

    # Convert the target path to be relative to PROJECT_ROOT for the container
    target_path_abs = Path(target_path).resolve()
    if not target_path_abs.is_relative_to(PROJECT_ROOT):
        raise exceptions.ParseError(
            msg=f"Path '{target_path}' must be within the project directory."
        )

    # Convert to container path
    container_path = str(target_path_abs.relative_to(PROJECT_ROOT))
    if container_path == ".":
        container_path = ""

    cmd = (
        f"{DOCKER_COMPOSE_CMD} run --rm -v "
        f'"{PROJECT_ROOT}:/tmp/project:rw" '
        f"odoo odoo scaffold {module_name} /tmp/project/{container_path}"
    )

    with c.cd(str(PROJECT_ROOT)):
        c.run(
            cmd,
            env=UID_ENV,
            pty=True,
        )
