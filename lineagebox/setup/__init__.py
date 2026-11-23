"""LineageBox's setup wizard (`lineagebox setup`, docs/install.md#the-setup-wizard): a page that asks where the photos,
the catalog and the AI helpers are, and writes the compose file that runs LineageBox there. compose.py makes the file,
profiles.py reads nas.toml (what the wizard knows of each NAS), host.py looks at the machine it runs on, and
server.py serves the page."""
import os

# LineageBox's image says LINEAGEBOX_SETUP=wizard (its Dockerfile), and a compose file made by the wizard, or written
# by hand from compose.manual.yaml, says LINEAGEBOX_SETUP: done
WIZARD, DONE = "wizard", "done"


def first_start(config_path=None):
    """Whether `lineagebox serve` should start the setup wizard instead of LineageBox: in LineageBox's image, until a
    compose file says LINEAGEBOX_SETUP: done, while no catalog folder is mounted. The repository's compose.yaml starts
    the image that way, so the wizard is the first thing people see. A compose file from before the wizard has no
    LINEAGEBOX_SETUP but mounts the catalog folder, so those installs start LineageBox as they always did; from source
    (no LINEAGEBOX_SETUP), it never comes up."""
    if os.environ.get("LINEAGEBOX_SETUP", "").strip().lower() != WIZARD:
        return False
    from lineagebox import config
    try:
        return not config.load(config_path).data_dir.is_dir()
    except (config.ConfigError, OSError):
        return False   # LineageBox's own start says what is wrong
