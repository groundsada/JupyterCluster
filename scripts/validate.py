#!/usr/bin/env python3
"""Pre-push validation script to catch errors before committing"""

import ast
import os
import sys
import subprocess
from pathlib import Path

# Colors for output
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def print_error(msg):
    print(f"{RED}✗ {msg}{RESET}")


def print_success(msg):
    print(f"{GREEN}✓ {msg}{RESET}")


def print_warning(msg):
    print(f"{YELLOW}⚠ {msg}{RESET}")


def check_python_syntax():
    """Check all Python files have valid syntax"""
    print("\n[1/6] Checking Python syntax...")
    errors = []
    repo_root = Path(__file__).parent.parent

    for py_file in repo_root.rglob("*.py"):
        # Skip __pycache__ and .git
        if "__pycache__" in str(py_file) or ".git" in str(py_file):
            continue

        try:
            with open(py_file, "r") as f:
                ast.parse(f.read(), py_file.name)
        except SyntaxError as e:
            errors.append(f"{py_file}:{e.lineno}: {e.msg}")
        except Exception as e:
            errors.append(f"{py_file}: {e}")

    if errors:
        for error in errors:
            print_error(error)
        return False

    print_success("All Python files have valid syntax")
    return True


def check_black_formatting():
    """Check code formatting with black"""
    print("\n[2/6] Checking code formatting (black)...")
    repo_root = Path(__file__).parent.parent

    result = subprocess.run(
        ["python3", "-m", "black", "--check", "jupytercluster/", "tests/"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print_error("Code formatting issues found")
        print(result.stdout)
        print(result.stderr)
        return False

    print_success("Code formatting is correct")
    return True


def check_isort():
    """Check import sorting"""
    print("\n[3/6] Checking import sorting (isort)...")
    repo_root = Path(__file__).parent.parent

    result = subprocess.run(
        ["python3", "-m", "isort", "--check-only", "jupytercluster/", "tests/"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print_error("Import sorting issues found")
        print(result.stdout)
        print(result.stderr)
        return False

    print_success("Import sorting is correct")
    return True


def check_templates_parse():
    """Check all templates can be parsed"""
    print("\n[4/6] Checking template syntax...")
    repo_root = Path(__file__).parent.parent
    template_dir = repo_root / "jupytercluster" / "templates"

    if not template_dir.exists():
        print_warning("Template directory not found")
        return True

    try:
        from tornado.template import Loader, ParseError

        loader = Loader(str(template_dir))
        errors = []

        for template_file in template_dir.glob("*.html"):
            try:
                # Try to load template
                loader.load(template_file.name)
            except ParseError as e:
                errors.append(f"{template_file.name}: {e.message} at line {e.lineno}")
            except Exception as e:
                errors.append(f"{template_file.name}: {type(e).__name__}: {e}")

        if errors:
            for error in errors:
                print_error(error)
            return False

        print_success("All templates parse correctly")
        return True

    except ImportError:
        print_warning("tornado not available, skipping template validation")
        return True


def check_templates_render():
    """Check templates can render with required variables"""
    print("\n[5/6] Checking template rendering...")
    repo_root = Path(__file__).parent.parent
    template_dir = repo_root / "jupytercluster" / "templates"

    if not template_dir.exists():
        return True

    try:
        from tornado.template import Loader

        loader = Loader(str(template_dir))

        def static_url(path):
            return f"/static/{path}"

        # Required variables for all templates extending page.html
        base_vars = {
            "base_url": "/",
            "user": None,
            "is_admin": False,
            "login_url": "/login",
            "logout_url": "/logout",
            "static_url": static_url,
            "announcement": None,
        }

        errors = []

        # Test each template
        templates_to_test = {
            "login.html": {
                **base_vars,
                "login_service": None,
                "authenticator_login_url": None,
                "login_error": None,
                "username": None,
            },
            "home.html": {
                **base_vars,
                "user": "testuser",
                "hubs": [],
                "all_hubs": [],
            },
            "error.html": {
                **base_vars,
                "status_code": 404,
                "error_message": "Not found",
            },
            "admin.html": {
                **base_vars,
                "user": "admin",
                "is_admin": True,
                "users": [],
                "hubs": [],
            },
            "hub_create.html": {
                **base_vars,
                "user": "testuser",
                "error": None,
            },
            "hub_detail.html": {
                **base_vars,
                "user": "testuser",
                "hub": type(
                    "Hub",
                    (),
                    {
                        "name": "test-hub",
                        "namespace": "jupyterhub-test-hub",
                        "owner": "testuser",
                        "status": "running",
                        "url": "http://test-hub.example.com",
                        "helm_chart": "jupyterhub/jupyterhub",
                        "helm_chart_version": "",
                        "description": "",
                        "created": "2024-01-01T00:00:00",
                        "last_activity": "2024-01-01T00:00:00",
                    },
                )(),
                "error": None,
            },
        }

        for template_name, vars_dict in templates_to_test.items():
            template_path = template_dir / template_name
            if not template_path.exists():
                continue

            try:
                html = loader.load(template_name).generate(**vars_dict)
                if html is None:
                    errors.append(f"{template_name}: Generated None")
            except Exception as e:
                errors.append(f"{template_name}: {type(e).__name__}: {e}")

        if errors:
            for error in errors:
                print_error(error)
            return False

        print_success("All templates render correctly")
        return True

    except ImportError:
        print_warning("tornado not available, skipping template rendering validation")
        return True


def check_common_issues():
    """Check for common issues"""
    print("\n[6/6] Checking for common issues...")
    repo_root = Path(__file__).parent.parent
    errors = []

    # Check for Jinja2 syntax in templates
    template_dir = repo_root / "jupytercluster" / "templates"
    if template_dir.exists():
        for template_file in template_dir.glob("*.html"):
            with open(template_file, "r") as f:
                content = f.read()
                # Check for Jinja2-only syntax
                if "{% endblock" in content or "{% endif" in content or "{% endfor" in content:
                    errors.append(
                        f"{template_file.name}: Contains Jinja2 syntax (should use {{% end %}})"
                    )
                if "|length" in content:
                    errors.append(
                        f"{template_file.name}: Uses |length filter (should use len())"
                    )
                if "loop.last" in content:
                    errors.append(
                        f"{template_file.name}: Uses loop.last (not supported in Tornado)"
                    )
                if "{% module Template" in content:
                    errors.append(
                        f"{template_file.name}: Contains invalid {{% module Template() %}} call"
                    )

    if errors:
        for error in errors:
            print_error(error)
        return False

    print_success("No common issues found")
    return True


def main():
    """Run all validation checks"""
    print("=" * 60)
    print("Pre-push Validation")
    print("=" * 60)

    checks = [
        check_python_syntax,
        check_black_formatting,
        check_isort,
        check_templates_parse,
        check_templates_render,
        check_common_issues,
    ]

    all_passed = True
    for check in checks:
        if not check():
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print_success("All validation checks passed!")
        return 0
    else:
        print_error("Validation failed! Fix errors before pushing.")
        print("\nTo auto-fix formatting issues, run:")
        print("  python3 -m black jupytercluster/ tests/")
        print("  python3 -m isort jupytercluster/ tests/")
        return 1


if __name__ == "__main__":
    sys.exit(main())

