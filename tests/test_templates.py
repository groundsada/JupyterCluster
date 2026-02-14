"""Test template parsing and rendering"""

import os
import pytest

try:
    from tornado.template import Template, ParseError, Loader
except ImportError:
    pytest.skip("tornado not available", allow_module_level=True)


class TestTemplates:
    """Test that all templates can be parsed and rendered"""

    @pytest.fixture
    def template_dir(self):
        """Get template directory path"""
        here = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(here, "jupytercluster", "templates")

    @pytest.fixture
    def loader(self, template_dir):
        """Create template loader"""
        return Loader(template_dir)

    def test_all_templates_parse(self, template_dir):
        """Test that all HTML templates can be parsed without errors"""
        template_files = [f for f in os.listdir(template_dir) if f.endswith(".html")]

        assert len(template_files) > 0, "No template files found"

        for template_file in template_files:
            template_path = os.path.join(template_dir, template_file)
            with open(template_path, "r") as f:
                content = f.read()

            try:
                # Try to parse the template
                Template(content, name=template_file)
            except ParseError as e:
                pytest.fail(
                    f"Template {template_file} failed to parse: {e.message} at line {e.lineno}"
                )
            except Exception as e:
                pytest.fail(
                    f"Template {template_file} raised unexpected error: {type(e).__name__}: {e}"
                )

    def test_login_template_renders(self, loader):
        """Test that login template can be rendered with required variables"""
        try:
            # Import static_url from tornado.web
            from tornado.web import static_url

            html = loader.load("login.html").generate(
                base_url="/",
                user=None,
                is_admin=False,
                login_url="/login",
                logout_url="/logout",
                login_service=None,
                authenticator_login_url=None,
                login_error=None,
                username=None,
                static_url=static_url,
            )
            # If we get here, rendering succeeded
            assert html is not None
        except ParseError as e:
            pytest.fail(f"Login template parse error: {e.message} at line {e.lineno}")
        except Exception as e:
            pytest.fail(f"Login template render error: {type(e).__name__}: {e}")

    def test_home_template_renders(self, loader):
        """Test that home template can be rendered"""
        try:
            from tornado.web import static_url

            html = loader.load("home.html").generate(
                base_url="/",
                user="testuser",
                is_admin=False,
                login_url="/login",
                logout_url="/logout",
                hubs=[],
                all_hubs=[],
                static_url=static_url,
            )
            assert html is not None
        except ParseError as e:
            pytest.fail(f"Home template parse error: {e.message} at line {e.lineno}")
        except Exception as e:
            pytest.fail(f"Home template render error: {type(e).__name__}: {e}")

    def test_error_template_renders(self, loader):
        """Test that error template can be rendered"""
        try:
            from tornado.web import static_url

            html = loader.load("error.html").generate(
                base_url="/",
                user=None,
                is_admin=False,
                login_url="/login",
                logout_url="/logout",
                status_code=404,
                error_message="Not found",
                static_url=static_url,
            )
            assert html is not None
        except ParseError as e:
            pytest.fail(f"Error template parse error: {e.message} at line {e.lineno}")
        except Exception as e:
            pytest.fail(f"Error template render error: {type(e).__name__}: {e}")

    def test_admin_template_renders(self, loader):
        """Test that admin template can be rendered"""
        try:
            from tornado.web import static_url

            html = loader.load("admin.html").generate(
                base_url="/",
                user="admin",
                is_admin=True,
                login_url="/login",
                logout_url="/logout",
                users=[],
                hubs=[],
                static_url=static_url,
            )
            assert html is not None
        except ParseError as e:
            pytest.fail(f"Admin template parse error: {e.message} at line {e.lineno}")
        except Exception as e:
            pytest.fail(f"Admin template render error: {type(e).__name__}: {e}")

    def test_hub_create_template_renders(self, loader):
        """Test that hub_create template can be rendered"""
        try:
            html = loader.load("hub_create.html").generate(
                base_url="/",
                user="testuser",
                is_admin=False,
                login_url="/login",
                logout_url="/logout",
                error=None,
            )
            assert html is not None
        except ParseError as e:
            pytest.fail(f"Hub create template parse error: {e.message} at line {e.lineno}")
        except Exception as e:
            pytest.fail(f"Hub create template render error: {type(e).__name__}: {e}")

    def test_hub_detail_template_renders(self, loader):
        """Test that hub_detail template can be rendered"""
        try:
            html = loader.load("hub_detail.html").generate(
                base_url="/",
                user="testuser",
                is_admin=False,
                login_url="/login",
                logout_url="/logout",
                hub={
                    "name": "test-hub",
                    "namespace": "jupyterhub-test-hub",
                    "owner": "testuser",
                    "status": "running",
                    "url": "http://test-hub.example.com",
                    "helm_chart": "jupyterhub/jupyterhub",
                    "created": "2024-01-01T00:00:00",
                    "last_activity": "2024-01-01T00:00:00",
                },
                error=None,
            )
            assert html is not None
        except ParseError as e:
            pytest.fail(f"Hub detail template parse error: {e.message} at line {e.lineno}")
        except Exception as e:
            pytest.fail(f"Hub detail template render error: {type(e).__name__}: {e}")
