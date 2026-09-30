from __future__ import annotations

from pathlib import Path

from edu_agent.template_spec import list_templates
from edu_agent.web_server import api_tpl_canvas


ROOT = Path(__file__).resolve().parents[1]


def test_builtin_lesson_templates_are_listed_as_rich_templates():
    templates = {item["template_id"]: item for item in list_templates()}

    for template_id in ("探究式课堂", "复习课", "习题讲评课"):
        assert template_id in templates
        assert templates[template_id]["rich"] is True
        assert templates[template_id]["blocks"] >= 8


def test_canvas_api_upgrades_skeleton_template_without_writing_it():
    result = api_tpl_canvas("default")

    assert result["ok"] is True
    assert result["source"] == "skeleton"
    assert result["template"]["spec_version"] == 2
    assert result["stats"]["blocks"] >= 6


def test_canvas_api_returns_rich_preset_unchanged():
    result = api_tpl_canvas("探究式课堂")

    assert result["ok"] is True
    assert result["source"] == "rich"
    assert result["template"]["template_id"] == "探究式课堂"
    assert result["stats"]["tables"] >= 1


def test_canvas_api_restores_style_from_original_docx():
    templates = {item["template_id"]: item for item in list_templates()}
    template_id = "高中数学_函数的概念_教案"

    assert templates[template_id]["original"] is True
    result = api_tpl_canvas(template_id)

    assert result["ok"] is True
    assert result["source"] == "original-docx"
    assert result["template"]["template_id"] == template_id
    assert result["stats"]["tables"] >= 3
    assert result["stats"]["paragraphs"] >= 10


def test_lesson_center_contains_multipage_controls_and_export_separator():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "lessonTemplateSelect",
        "lessonApplyTemplate",
        "lessonAddPage",
        "lessonDuplicatePage",
        "lessonDeletePage",
        "lessonPageState",
        "lessonPages",
    ):
        assert f'id="{element_id}"' in html
    assert "function documentHtml()" in html
    assert '<div class="lesson-page-break"></div>' in html
    assert "version:4" in html


def test_lesson_center_contains_library_and_agent_reference_integration():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "lessonLibraryOpen",
        "lessonLibraryLayer",
        "lessonLibrarySave",
        "lessonLibraryImport",
        "lessonLibrarySearch",
        "lessonLibraryList",
        "lessonLibraryAskAgent",
    ):
        assert f'id="{element_id}"' in html
    assert "function libraryHtmlToPages" in html
    assert "libraryRequest('/api/lesson-library'" in html
    assert "reference_ids:Array.from(libraryRefs)" in html


def test_agent_b_uses_a_persistent_draggable_floating_button():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'class="lesson-agent-fab" id="lessonAgentToggle"' in html
    assert 'id="lessonAgentToggleLabel"' in html
    assert "edu_lesson_agent_fab_pos_v1" in html
    assert "function bindAgentToggleDrag()" in html
    assert "bindAgentToggleDrag();bindPanelDrag()" in html
    assert ".lesson-agent-fab.active{opacity:0;visibility:hidden;pointer-events:none" in html


def test_lesson_center_separates_plan_and_ppt_but_keeps_one_agent_b_context():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "lessonCenterNav",
        "lessonCenterToggle",
        "lessonCenterChildren",
        "lessonWorkspacePlan",
        "lessonWorkspacePpt",
        "lessonPptStudio",
        "lessonPptRefresh",
        "lessonPptExport",
    ):
        assert f'id="{element_id}"' in html
    assert "function openSurface(next)" in html
    assert "window.__lessonWorkspaceSurface=setSurface" in html
    assert 'data-lesson-surface="plan"' in html
    assert 'data-lesson-surface="ppt"' in html
    assert "payload:payload()" in html
    assert "/api/plan/assistant" in html
    assert "history:history.slice(-10)" in html
