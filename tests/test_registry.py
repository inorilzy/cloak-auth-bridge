import pytest

from cloak_auth_bridge.config import ProfileConfig, Registry


def test_registry_allows_only_declared_site_profile_edges() -> None:
    registry = Registry.load()

    site, profile = registry.target("bilibili-main", "bilibili-main")
    assert site.id == "bilibili-main"
    assert profile.dedicated is True

    with pytest.raises(ValueError, match="unknown target_profile"):
        registry.target("bilibili-main", "arbitrary-path")


def test_profile_paths_cannot_escape_profiles_directory() -> None:
    profile = ProfileConfig(path="../outside", allowedSites=["bilibili-main"])

    with pytest.raises(ValueError, match="inside profiles"):
        Registry.resolve_profile_path(profile)


def test_registered_profile_path_is_inside_profiles() -> None:
    registry = Registry.load()
    path = registry.resolve_profile_path(registry.profiles["bilibili-main"])
    assert path.name == "bilibili-main"
    assert path.parent.name == "profiles"


def test_dedicated_profile_must_allow_exactly_one_site() -> None:
    with pytest.raises(ValueError, match="exactly one site"):
        ProfileConfig(
            path="profiles/shared",
            allowedSites=["bilibili-main", "another-site"],
            dedicated=True,
        )
