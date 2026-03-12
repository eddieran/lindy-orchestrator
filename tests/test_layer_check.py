"""Tests for the layer check QA gate."""

from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.qa.layer_check import (
    LayerDef,
    LayerCheckGate,
    _check_layer_violations,
    _extract_intra_imports,
    _parse_architecture_layers,
    _resolve_layer,
)
from lindy_orchestrator.qa.structural_check import Violation, _format_violations


# ---------------------------------------------------------------------------
# _parse_architecture_layers
# ---------------------------------------------------------------------------


class TestParseArchitectureLayers:
    def test_fastapi_layers(self, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **backend/**: models → schemas → services → routes → main\n"
        )
        result = _parse_architecture_layers(tmp_path, "backend")
        assert result is not None
        assert result.module == "backend"
        assert result.layers == ["models", "schemas", "services", "routes", "main"]

    def test_react_layers(self, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **frontend/**: types → hooks → components → pages → app\n"
        )
        result = _parse_architecture_layers(tmp_path, "frontend")
        assert result is not None
        assert result.layers == ["types", "hooks", "components", "pages", "app"]

    def test_django_layers(self, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **api/**: models → serializers → views → urls → wsgi\n"
        )
        result = _parse_architecture_layers(tmp_path, "api")
        assert result is not None
        assert result.layers == ["models", "serializers", "views", "urls", "wsgi"]

    def test_no_architecture_md(self, tmp_path: Path):
        result = _parse_architecture_layers(tmp_path, "backend")
        assert result is None

    def test_no_matching_module(self, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **frontend/**: types → hooks → components → pages → app\n"
        )
        result = _parse_architecture_layers(tmp_path, "backend")
        assert result is None

    def test_multiple_modules(self, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n"
            "- **backend/**: models → schemas → services → routes → main\n"
            "- **frontend/**: types → hooks → components → pages → app\n"
        )
        be = _parse_architecture_layers(tmp_path, "backend")
        fe = _parse_architecture_layers(tmp_path, "frontend")
        assert be is not None
        assert fe is not None
        assert be.layers[0] == "models"
        assert fe.layers[0] == "types"

    def test_ascii_arrow(self, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n"
            "- **backend/**: models -> schemas -> services -> routes -> main\n"
        )
        result = _parse_architecture_layers(tmp_path, "backend")
        assert result is not None
        assert result.layers == ["models", "schemas", "services", "routes", "main"]


# ---------------------------------------------------------------------------
# _resolve_layer
# ---------------------------------------------------------------------------


class TestResolveLayer:
    def test_direct_match(self):
        layers = ["models", "schemas", "services", "routes", "main"]
        assert _resolve_layer("models/user.py", layers) == 0
        assert _resolve_layer("routes/api.py", layers) == 3
        assert _resolve_layer("main.py", layers) == 4

    def test_nested_path(self):
        layers = ["models", "schemas", "services", "routes", "main"]
        assert _resolve_layer("services/auth/handler.py", layers) == 2

    def test_utils_exempt(self):
        layers = ["models", "schemas", "services", "routes", "main"]
        assert _resolve_layer("utils/helpers.py", layers) == -1

    def test_shared_exempt(self):
        layers = ["models", "schemas", "services", "routes", "main"]
        assert _resolve_layer("shared/constants.py", layers) == -1

    def test_common_exempt(self):
        layers = ["models", "schemas", "services", "routes", "main"]
        assert _resolve_layer("common/config.py", layers) == -1

    def test_unknown_file(self):
        layers = ["models", "schemas", "services", "routes", "main"]
        assert _resolve_layer("random/file.py", layers) is None


# ---------------------------------------------------------------------------
# _extract_intra_imports
# ---------------------------------------------------------------------------


class TestExtractIntraImports:
    def test_python_imports(self, tmp_path: Path):
        f = tmp_path / "service.py"
        f.write_text(
            "from backend.models import User\nfrom backend.schemas import UserSchema\nimport os\n"
        )
        imports = _extract_intra_imports(f, "backend/")
        assert len(imports) == 2
        assert "backend.models" in imports
        assert "backend.schemas" in imports

    def test_js_relative_imports(self, tmp_path: Path):
        f = tmp_path / "component.tsx"
        f.write_text(
            "import { Button } from './components/Button'\n"
            "import { useAuth } from '../hooks/useAuth'\n"
            "import React from 'react'\n"
        )
        imports = _extract_intra_imports(f, "frontend/")
        assert len(imports) == 2

    def test_non_code_file(self, tmp_path: Path):
        f = tmp_path / "readme.md"
        f.write_text("# Hello")
        imports = _extract_intra_imports(f, "backend/")
        assert imports == []

    def test_nonexistent_file(self, tmp_path: Path):
        f = tmp_path / "missing.py"
        imports = _extract_intra_imports(f, "backend/")
        assert imports == []


# ---------------------------------------------------------------------------
# _check_layer_violations
# ---------------------------------------------------------------------------


class TestCheckLayerViolations:
    def test_valid_downward_import(self, tmp_path: Path):
        """services → models is valid (lower layer)."""
        mod = tmp_path / "backend"
        svc = mod / "services"
        svc.mkdir(parents=True)
        f = svc / "user_service.py"
        f.write_text("from backend.models import User\n")

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["backend/services/user_service.py"]
        )
        assert violations == []

    def test_invalid_upward_import(self, tmp_path: Path):
        """models → routes is invalid (higher layer)."""
        mod = tmp_path / "backend"
        models = mod / "models"
        models.mkdir(parents=True)
        f = models / "user.py"
        f.write_text("from backend.routes import api_router\n")

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["backend/models/user.py"]
        )
        assert len(violations) == 1
        assert violations[0].rule == "layer_violation"
        assert "models" in violations[0].message
        assert "routes" in violations[0].message
        assert (
            "remediation" in violations[0].remediation.lower()
            or "Move" in violations[0].remediation
        )

    def test_same_layer_import(self, tmp_path: Path):
        """services → services is valid (same layer)."""
        mod = tmp_path / "backend"
        svc = mod / "services"
        svc.mkdir(parents=True)
        f = svc / "auth.py"
        f.write_text("from backend.services.user import get_user\n")

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["backend/services/auth.py"]
        )
        assert violations == []

    def test_utils_exempt_import(self, tmp_path: Path):
        """Any layer can import from utils/."""
        mod = tmp_path / "backend"
        models = mod / "models"
        models.mkdir(parents=True)
        f = models / "user.py"
        f.write_text("from backend.utils import helpers\n")

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["backend/models/user.py"]
        )
        assert violations == []

    def test_tests_excluded(self, tmp_path: Path):
        """Files under tests/ should not be checked."""
        mod = tmp_path / "backend"
        tests = mod / "tests"
        tests.mkdir(parents=True)
        f = tests / "test_routes.py"
        f.write_text("from backend.routes import api_router\nfrom backend.models import User\n")

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["backend/tests/test_routes.py"]
        )
        assert violations == []

    def test_files_outside_module_skipped(self, tmp_path: Path):
        """Files not in the module prefix are skipped."""
        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["frontend/components/App.tsx"]
        )
        assert violations == []

    def test_main_can_import_everything(self, tmp_path: Path):
        """main (highest layer) can import from any layer."""
        mod = tmp_path / "backend"
        mod.mkdir(parents=True)
        f = mod / "main.py"
        f.write_text(
            "from backend.models import User\n"
            "from backend.routes import router\n"
            "from backend.services import auth\n"
        )

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(tmp_path, "backend", layer_def, ["backend/main.py"])
        assert violations == []

    def test_tsx_upward_violation(self, tmp_path: Path):
        """React: types → pages is invalid (higher layer via relative import)."""
        mod = tmp_path / "frontend"
        types_dir = mod / "types"
        types_dir.mkdir(parents=True)
        f = types_dir / "user.tsx"
        f.write_text("import { UserPage } from '../pages/UserPage'\n")

        layer_def = LayerDef(
            module="frontend",
            layers=["types", "hooks", "components", "pages", "app"],
        )
        violations = _check_layer_violations(
            tmp_path, "frontend", layer_def, ["frontend/types/user.tsx"]
        )
        assert len(violations) == 1
        assert "types" in violations[0].message
        assert "pages" in violations[0].message

    def test_tsx_downward_valid(self, tmp_path: Path):
        """React: pages → hooks is valid (lower layer via relative import)."""
        mod = tmp_path / "frontend"
        pages_dir = mod / "pages"
        pages_dir.mkdir(parents=True)
        f = pages_dir / "Dashboard.tsx"
        f.write_text("import { useAuth } from '../hooks/useAuth'\n")

        layer_def = LayerDef(
            module="frontend",
            layers=["types", "hooks", "components", "pages", "app"],
        )
        violations = _check_layer_violations(
            tmp_path, "frontend", layer_def, ["frontend/pages/Dashboard.tsx"]
        )
        assert violations == []

    def test_multiple_violations_single_file(self, tmp_path: Path):
        """One file with multiple upward imports produces multiple violations."""
        mod = tmp_path / "backend"
        models_dir = mod / "models"
        models_dir.mkdir(parents=True)
        f = models_dir / "base.py"
        f.write_text("from backend.routes import router\nfrom backend.services import auth\n")

        layer_def = LayerDef(
            module="backend",
            layers=["models", "schemas", "services", "routes", "main"],
        )
        violations = _check_layer_violations(
            tmp_path, "backend", layer_def, ["backend/models/base.py"]
        )
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# _format_violations
# ---------------------------------------------------------------------------


class TestFormatViolations:
    def test_no_violations(self):
        result = _format_violations([], label="layer")
        assert "passed" in result.lower()

    def test_formats_violations(self):
        violations = [
            Violation(
                rule="layer_violation",
                file="backend/models/user.py",
                message="models imports from routes (higher).",
                remediation="Move shared logic to models/ or lower.",
            )
        ]
        result = _format_violations(violations, label="layer")
        assert "1 layer violation(s)" in result
        assert "VIOLATION [layer_violation]" in result
        assert "FIX: Move" in result


# ---------------------------------------------------------------------------
# LayerCheckGate
# ---------------------------------------------------------------------------


class TestLayerCheckGate:
    def test_no_project_root(self):
        gate = LayerCheckGate()
        result = gate.check(params={}, project_root=None)
        assert not result.passed

    def test_disabled(self, tmp_path: Path):
        gate = LayerCheckGate()
        result = gate.check(
            params={"enabled": False},
            project_root=tmp_path,
            module_name="backend",
        )
        assert result.passed
        assert "disabled" in result.output.lower()

    def test_no_architecture_md(self, tmp_path: Path):
        gate = LayerCheckGate()
        result = gate.check(
            params={},
            project_root=tmp_path,
            module_name="backend",
        )
        assert result.passed
        assert "No layer definition" in result.output

    @patch("lindy_orchestrator.qa.layer_check._get_staged_files")
    def test_pass_valid_imports(self, mock_staged, tmp_path: Path):
        # Create ARCHITECTURE.md
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **backend/**: models → schemas → services → routes → main\n"
        )

        # Create valid module structure
        mod = tmp_path / "backend" / "services"
        mod.mkdir(parents=True)
        f = mod / "auth.py"
        f.write_text("from backend.models import User\n")

        mock_staged.return_value = ["backend/services/auth.py"]

        gate = LayerCheckGate()
        result = gate.check(
            params={},
            project_root=tmp_path,
            module_name="backend",
        )
        assert result.passed

    @patch("lindy_orchestrator.qa.layer_check._get_staged_files")
    def test_fail_invalid_imports(self, mock_staged, tmp_path: Path):
        # Create ARCHITECTURE.md
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **backend/**: models → schemas → services → routes → main\n"
        )

        # Create invalid module structure
        mod = tmp_path / "backend" / "models"
        mod.mkdir(parents=True)
        f = mod / "user.py"
        f.write_text("from backend.routes import api_router\n")

        mock_staged.return_value = ["backend/models/user.py"]

        gate = LayerCheckGate()
        result = gate.check(
            params={},
            project_root=tmp_path,
            module_name="backend",
        )
        assert not result.passed
        assert result.details["violation_count"] == 1

    @patch("lindy_orchestrator.qa.layer_check._get_staged_files")
    def test_qa_result_has_layers_detail(self, mock_staged, tmp_path: Path):
        arch = tmp_path / ".orchestrator" / "architecture.md"
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(
            "## Layer Structure\n\n- **backend/**: models → schemas → services → routes → main\n"
        )
        mod = tmp_path / "backend" / "models"
        mod.mkdir(parents=True)
        (mod / "user.py").write_text("x = 1\n")

        mock_staged.return_value = ["backend/models/user.py"]

        gate = LayerCheckGate()
        result = gate.check(params={}, project_root=tmp_path, module_name="backend")
        assert result.passed
        assert result.details["layers"] == ["models", "schemas", "services", "routes", "main"]


# ---------------------------------------------------------------------------
# Gate registration
# ---------------------------------------------------------------------------


class TestGateRegistration:
    def test_registered(self):
        from lindy_orchestrator.qa import _GATES

        assert "layer_check" in _GATES
