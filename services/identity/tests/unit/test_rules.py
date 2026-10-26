from __future__ import annotations

import pytest

from identity.accounts.model import display_name_problem, password_problem, username_problem


@pytest.mark.parametrize("name", ["ann", "a_b_c", "x" * 30, "user_42"])
def test_valid_usernames(name: str) -> None:
    assert username_problem(name) is None


@pytest.mark.parametrize("name", ["ab", "x" * 31, "bad name", "ann@home", " ann", "Ann", "ünï"])
def test_invalid_usernames(name: str) -> None:
    assert username_problem(name) is not None


@pytest.mark.parametrize("password", ["Secret123", "aB3" + "x" * 5, "Aa1" + "z" * 125])
def test_valid_passwords(password: str) -> None:
    assert password_problem(password) is None


@pytest.mark.parametrize(
    "password", ["password", "PASSWORD1", "password1", "Password", "Ab1", "Aa1" + "z" * 126]
)
def test_invalid_passwords(password: str) -> None:
    assert password_problem(password) is not None


def test_display_name_bounds() -> None:
    assert display_name_problem("A") is None
    assert display_name_problem("") is not None
    assert display_name_problem("x" * 51) is not None
