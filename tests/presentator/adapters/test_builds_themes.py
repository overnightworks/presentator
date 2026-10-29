"""Which theme packages the toolchain project's own manifest names."""

from pathlib import Path

import pytest

from presentator.adapters import builds as builds_module
from presentator.adapters.builds import PackageJsonThemes


def test_theme_packages_are_named_by_what_follows_the_slidev_theme_prefix() -> None:
    manifest = """
    {
      "devDependencies": {
        "@slidev/theme-seriph": "0.25.0",
        "@slidev/cli": "52.19.1"
      },
      "dependencies": {
        "@slidev/theme-apple-basic": "0.25.1"
      }
    }
    """

    assert builds_module.theme_names_of_manifest(manifest) == (
        "apple-basic",
        "default",
        "seriph",
    )


def test_default_is_named_even_when_the_manifest_does_not_list_it() -> None:
    manifest = '{"devDependencies": {"vue": "3.5.42"}}'

    assert builds_module.theme_names_of_manifest(manifest) == ("default",)


def test_malformed_json_is_not_a_set_to_guess_from() -> None:
    with pytest.raises(ValueError, match="Expecting value"):
        builds_module.theme_names_of_manifest("not json at all")


@pytest.mark.parametrize(
    "manifest",
    [
        "[]",
        '{"devDependencies": ["@slidev/theme-seriph"]}',
        '{"devDependencies": {"@slidev/theme-seriph": ["0", "25", "0"]}}',
    ],
    ids=[
        "not an object",
        "a section that is not an object",
        "a version that is not a string",
    ],
)
def test_a_manifest_shape_package_json_does_not_carry_is_not_a_set_to_guess_from(
    manifest: str,
) -> None:
    with pytest.raises(TypeError, match="not"):
        builds_module.theme_names_of_manifest(manifest)


def test_the_themes_are_read_off_the_toolchain_projects_own_manifest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "slidev"
    project.mkdir()
    (project / "package.json").write_text(
        '{"devDependencies": {"@slidev/theme-seriph": "0.25.0"}}',
    )

    assert PackageJsonThemes(project=project).names() == ("default", "seriph")


def test_a_toolchain_project_this_server_cannot_read_names_no_themes(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "no-such-project"

    assert PackageJsonThemes(project=missing).names() is None


def test_a_dependency_section_that_is_not_a_mapping_names_no_themes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "slidev"
    project.mkdir()
    (project / "package.json").write_text(
        '{"dependencies": ["@slidev/theme-seriph"]}',
    )

    assert PackageJsonThemes(project=project).names() is None
