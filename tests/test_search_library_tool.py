"""Unit tests for SearchLibraryTool — schema generation and collection routing."""

from unittest.mock import MagicMock, patch
import pytest

from local.tools.search_library_tool import SearchLibraryTool


def _make_tool(collections: list[dict] | None = None) -> SearchLibraryTool:
    """Build a SearchLibraryTool with mocked bus and configurable collections."""
    with patch("local.tools.search_library_tool.make_participant_bus") as mock_bus:
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_bus.return_value = (mock_pub, mock_sub)
        tool = SearchLibraryTool(document_service=MagicMock())
        tool._pub = mock_pub
        tool._sub = mock_sub

    cfg = {"collections": collections} if collections is not None else {}
    with patch("local.tools.search_library_tool.get_config", return_value=cfg):
        tool._cfg_override = cfg
    return tool


def _schema_for(tool: SearchLibraryTool, collections: list[dict]) -> dict:
    cfg = {"collections": collections}
    with patch("local.tools.search_library_tool.get_config", return_value=cfg):
        return tool._build_schema()


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------

class TestSchemaGeneration:
    def setup_method(self):
        self.tool = _make_tool()

    def test_zero_collections_no_enum(self):
        schema = _schema_for(self.tool, [])
        params = schema["function"]["parameters"]["properties"]
        assert "collection" not in params
        assert "query" in params

    def test_single_collection_no_enum(self):
        cols = [{"name": "mba", "display_name": "MBA Textbooks",
                 "description": "MBA strategy and finance"}]
        schema = _schema_for(self.tool, cols)
        params = schema["function"]["parameters"]["properties"]
        assert "collection" not in params
        assert "MBA Textbooks" in schema["function"]["description"]

    def test_single_collection_description_in_tool_description(self):
        cols = [{"name": "mba", "display_name": "MBA Textbooks",
                 "description": "strategy and finance"}]
        schema = _schema_for(self.tool, cols)
        assert "strategy and finance" in schema["function"]["description"]

    def test_multiple_collections_adds_enum(self):
        cols = [
            {"name": "mba", "display_name": "MBA Textbooks", "description": "strategy"},
            {"name": "econ", "display_name": "Economics", "description": "macro theory"},
        ]
        schema = _schema_for(self.tool, cols)
        params = schema["function"]["parameters"]["properties"]
        assert "collection" in params
        assert params["collection"]["type"] == "string"
        assert set(params["collection"]["enum"]) == {"mba", "econ"}

    def test_enum_description_contains_all_collections(self):
        cols = [
            {"name": "mba", "display_name": "MBA Textbooks", "description": "strategy"},
            {"name": "econ", "display_name": "Economics", "description": "macro theory"},
        ]
        schema = _schema_for(self.tool, cols)
        enum_desc = schema["function"]["parameters"]["properties"]["collection"]["description"]
        assert "mba" in enum_desc
        assert "econ" in enum_desc

    def test_collection_not_required(self):
        cols = [
            {"name": "mba", "display_name": "MBA", "description": "strategy"},
            {"name": "econ", "display_name": "Econ", "description": "macro"},
        ]
        schema = _schema_for(self.tool, cols)
        required = schema["function"]["parameters"].get("required", [])
        assert "collection" not in required
        assert "query" in required


# ---------------------------------------------------------------------------
# Collection routing in _search
# ---------------------------------------------------------------------------

class TestCollectionRouting:
    def setup_method(self):
        self.docs = MagicMock()
        with patch("local.tools.search_library_tool.make_participant_bus") as mock_bus:
            mock_bus.return_value = (MagicMock(), MagicMock())
            self.tool = SearchLibraryTool(document_service=self.docs)
        self.docs.count.return_value = 10

    def test_collection_passed_to_search(self):
        self.docs.search.return_value = []
        with patch("local.tools.search_library_tool.get_config", return_value={}):
            self.tool._search("strategy", collection="mba")
        self.docs.search.assert_called_once_with("strategy", collection="mba")

    def test_no_collection_passes_none(self):
        self.docs.search.return_value = []
        with patch("local.tools.search_library_tool.get_config", return_value={}):
            self.tool._search("strategy", collection=None)
        self.docs.search.assert_called_once_with("strategy", collection=None)

    def test_result_header_shows_collection_display_name(self):
        self.docs.search.return_value = [
            {"content": "some text", "source_file": "a.pdf",
             "collection": "mba", "chunk_index": 0, "score": 0.9}
        ]
        cfg = {"collections": [{"name": "mba", "display_name": "MBA Textbooks",
                                 "description": "strategy"}]}
        with patch("local.tools.search_library_tool.get_config", return_value=cfg):
            result = self.tool._search("strategy", collection="mba")
        assert "MBA Textbooks" in result

    def test_empty_query_returns_error(self):
        with patch("local.tools.search_library_tool.get_config", return_value={}):
            result = self.tool._search("", collection=None)
        assert "query is required" in result

    def test_empty_library_returns_message(self):
        self.docs.count.return_value = 0
        with patch("local.tools.search_library_tool.get_config", return_value={}):
            result = self.tool._search("anything", collection=None)
        assert "empty" in result.lower()
