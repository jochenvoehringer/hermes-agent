"""A config command must honor an explicitly selected Hermes root."""

import os
import subprocess
import sys


def test_config_does_not_follow_sticky_profile_from_explicit_root(tmp_path):
    root = tmp_path / ".hermes"
    root.mkdir()
    (root / "active_profile").write_text("router\n", encoding="utf-8")
    profile = root / "profiles" / "router"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("{}\n", encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["HERMES_HOME"] = str(root)
    env.pop("HERMES_SUPERVISED_CHILD", None)
    env.pop("HERMES_S6_SUPERVISED_CHILD", None)
    code = (
        "import sys; sys.argv=['hermes','config','get','display.skin']; "
        "from hermes_cli.main import _apply_profile_override; "
        "_apply_profile_override(); "
        "import os; print(os.environ['HERMES_HOME'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.getcwd(), env=env, capture_output=True, text=True, check=True,
    )

    assert result.stdout.strip() == str(root)
