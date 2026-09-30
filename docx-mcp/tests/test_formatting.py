"""Tests for table and cell formatting functionality."""

import pytest
from pathlib import Path

from docx_mcp.models.responses import ResponseStatus
from docx_mcp.models.formatting import (
    TextFormat, CellAlignment, CellBorders, BorderProperties,
    HorizontalAlignment, VerticalAlignment, BorderStyle, BorderWidth,
    Colors, Fonts
)
from docx_mcp.operations.tables.formatting import TableFormattingOperations


class TestCellTextFormatting:
    """Test cell text formatting operations."""
    
    def test_format_cell_text_basic(self, document_manager, table_operations, test_doc_path):
        """Test basic text formatting using set_cell_value."""
        # Create document and table
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        # Apply text formatting using set_cell_value
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            font_family=Fonts.ARIAL,
            font_size=14,
            font_color=Colors.RED,
            bold=True,
            italic=True
        )
        
        assert result.status == ResponseStatus.SUCCESS
        assert "Cell value and formatting set" in result.message
        assert result.data["table_index"] == 0
        assert result.data["row_index"] == 0
        assert result.data["column_index"] == 0
    
    def test_format_cell_text_invalid_cell(self, document_manager, table_operations, test_doc_path):
        """Test text formatting with invalid cell position."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 5, 5, "Test",  # Invalid position
            bold=True
        )
        
        assert result.status == ResponseStatus.ERROR
    
    def test_format_cell_text_from_dict(self, document_manager, table_operations, test_doc_path):
        """Test text formatting using set_cell_value parameters."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            font_family="Times New Roman",
            font_size=16,
            bold=True,
            underline=True
        )
        
        assert result.status == ResponseStatus.SUCCESS


class TestCellAlignment:
    """Test cell alignment operations using set_cell_value."""
    
    def test_format_cell_alignment_horizontal(self, document_manager, table_operations, test_doc_path):
        """Test horizontal alignment."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            horizontal_alignment="center"
        )
        
        assert result.status == ResponseStatus.SUCCESS
        assert "Cell value and formatting set" in result.message
    
    def test_format_cell_alignment_vertical(self, document_manager, table_operations, test_doc_path):
        """Test vertical alignment."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            vertical_alignment="middle"
        )
        
        assert result.status == ResponseStatus.SUCCESS
    
    def test_format_cell_alignment_both(self, document_manager, table_operations, test_doc_path):
        """Test both horizontal and vertical alignment."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            horizontal_alignment="right",
            vertical_alignment="bottom"
        )
        
        assert result.status == ResponseStatus.SUCCESS


class TestCellBackground:
    """Test cell background color operations using set_cell_value."""
    
    def test_format_cell_background_valid_color(self, document_manager, table_operations, test_doc_path):
        """Test setting valid background color."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            background_color=Colors.YELLOW
        )
        
        assert result.status == ResponseStatus.SUCCESS
        assert "Cell value and formatting set" in result.message
    
    def test_format_cell_background_with_hash(self, document_manager, table_operations, test_doc_path):
        """Test setting background color with # prefix."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            background_color="#00FF00"  # Green with #
        )
        
        assert result.status == ResponseStatus.SUCCESS
    
    def test_format_cell_background_invalid_color(self, document_manager, table_operations, test_doc_path):
        """Test setting invalid background color."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            background_color="invalid_color"
        )
        
        # Note: Invalid colors are handled gracefully in set_cell_value
        assert result.status == ResponseStatus.SUCCESS


class TestCellBorders:
    """Test cell border operations using set_cell_value."""
    
    def test_format_cell_borders_single_side(self, document_manager, table_operations, test_doc_path):
        """Test setting border for single side."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            top_style="solid",
            top_width="thick",
            top_color=Colors.BLACK
        )
        
        assert result.status == ResponseStatus.SUCCESS
        assert "Cell value and formatting set" in result.message
    
    def test_format_cell_borders_all_sides(self, document_manager, table_operations, test_doc_path):
        """Test setting borders for all sides."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            top_style="double",
            top_width="medium",
            top_color=Colors.BLUE,
            bottom_style="double",
            bottom_width="medium",
            bottom_color=Colors.BLUE,
            left_style="double",
            left_width="medium",
            left_color=Colors.BLUE,
            right_style="double",
            right_width="medium",
            right_color=Colors.BLUE
        )
        
        assert result.status == ResponseStatus.SUCCESS
    
    def test_format_cell_borders_from_dict(self, document_manager, table_operations, test_doc_path):
        """Test setting borders using set_cell_value parameters."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            top_style="solid",
            top_width="thick",
            top_color="FF0000",
            bottom_style="dashed",
            bottom_width="thin",
            bottom_color="00FF00"
        )
        
        assert result.status == ResponseStatus.SUCCESS


class TestCompleteFormatting:
    """Test complete cell formatting operations using set_cell_value."""
    
    def test_format_cell_complete(self, document_manager, table_operations, test_doc_path):
        """Test applying complete formatting to a cell."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Formatted Cell",
            font_family="Arial",
            font_size=12,
            font_color="000080",
            bold=True,
            italic=False,
            horizontal_alignment="center",
            vertical_alignment="middle",
            background_color="F0F0F0",
            top_style="solid",
            top_width="medium",
            top_color="000000",
            bottom_style="solid",
            bottom_width="medium",
            bottom_color="000000",
            left_style="solid",
            left_width="medium",
            left_color="000000",
            right_style="solid",
            right_width="medium",
            right_color="000000"
        )
        
        assert result.status == ResponseStatus.SUCCESS
        assert "Cell value and formatting set" in result.message
    
    def test_format_cell_complete_partial(self, document_manager, table_operations, test_doc_path):
        """Test applying partial complete formatting."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        result = table_operations.set_cell_value(
            str(test_doc_path), 0, 0, 0, "Test Text",
            bold=True,
            font_size=16,
            background_color="FFFF00"
        )
        
        assert result.status == ResponseStatus.SUCCESS


class TestBatchFormatting:
    """Test batch formatting operations using set_multiple_cells."""
    
    def test_set_multiple_cells_basic(self, document_manager, table_operations, test_doc_path):
        """Test setting multiple cells with basic formatting."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=3, cols=3)
        
        cells = [
            {
                "row_index": 0,
                "column_index": 0,
                "value": "Header 1",
                "bold": True,
                "background_color": "CCCCCC"
            },
            {
                "row_index": 0,
                "column_index": 1,
                "value": "Header 2",
                "bold": True,
                "background_color": "CCCCCC"
            },
            {
                "row_index": 1,
                "column_index": 0,
                "value": "Data 1",
                "font_size": 12
            },
            {
                "row_index": 1,
                "column_index": 1,
                "value": "Data 2",
                "font_size": 12
            }
        ]
        
        result = table_operations.set_multiple_cells(
            str(test_doc_path), 0, cells
        )
        
        assert result.status == ResponseStatus.SUCCESS
        assert result.data["total_cells"] == 4
        assert result.data["successful_cells"] == 4
        assert result.data["failed_cells"] == 0
    
    def test_set_multiple_cells_with_errors(self, document_manager, table_operations, test_doc_path):
        """Test setting multiple cells with some errors."""
        document_manager.open_document(str(test_doc_path), create_if_not_exists=True)
        table_operations.create_table(str(test_doc_path), rows=2, cols=2)
        
        cells = [
            {
                "row_index": 0,
                "column_index": 0,
                "value": "Valid Cell",
                "bold": True
            },
            {
                "row_index": 5,  # Invalid row
                "column_index": 5,  # Invalid column
                "value": "Invalid Cell"
            },
            {
                "row_index": 1,
                "column_index": 1,
                "value": "Another Valid Cell",
                "italic": True
            }
        ]
        
        result = table_operations.set_multiple_cells(
            str(test_doc_path), 0, cells
        )
        
        assert result.status == ResponseStatus.SUCCESS  # Partial success
        assert result.data["total_cells"] == 3
        assert result.data["successful_cells"] == 2
        assert result.data["failed_cells"] == 1
        assert len(result.data["errors"]) == 1


class TestFormattingModels:
    """Test formatting data models."""
    
    def test_text_format_to_dict(self):
        """Test TextFormat to_dict conversion."""
        text_format = TextFormat(
            font_family="Arial",
            font_size=12,
            bold=True,
            italic=False
        )
        
        result = text_format.to_dict()
        
        assert result["font_family"] == "Arial"
        assert result["font_size"] == 12
        assert result["bold"] is True
        assert result["italic"] is False
        assert "underline" not in result  # None values should be excluded
    
    def test_text_format_from_dict(self):
        """Test TextFormat from_dict conversion."""
        data = {
            "font_family": "Times New Roman",
            "font_size": 14,
            "font_color": "FF0000",
            "bold": True,
            "unknown_field": "ignored"  # Should be ignored
        }
        
        text_format = TextFormat.from_dict(data)
        
        assert text_format.font_family == "Times New Roman"
        assert text_format.font_size == 14
        assert text_format.font_color == "FF0000"
        assert text_format.bold is True
        assert text_format.italic is None  # Not in dict
    
    def test_cell_alignment_to_dict(self):
        """Test CellAlignment to_dict conversion."""
        alignment = CellAlignment(
            horizontal=HorizontalAlignment.CENTER,
            vertical=VerticalAlignment.MIDDLE
        )
        
        result = alignment.to_dict()
        
        assert result["horizontal"] == "center"
        assert result["vertical"] == "middle"
    
    def test_border_properties_from_dict(self):
        """Test BorderProperties from_dict conversion."""
        data = {
            "style": "dashed",
            "width": "thick",
            "color": "FF0000"
        }
        
        border = BorderProperties.from_dict(data)
        
        assert border.style == BorderStyle.DASHED
        assert border.width == BorderWidth.THICK
        assert border.color == "FF0000"


class TestColorValidation:
    """Test color validation utilities."""
    
    def test_validate_color_valid(self):
        """Test validation of valid colors."""
        from docx_mcp.models.formatting import validate_color
        
        assert validate_color("FF0000") is True
        assert validate_color("00FF00") is True
        assert validate_color("0000FF") is True
        assert validate_color("FFFFFF") is True
        assert validate_color("000000") is True
    
    def test_validate_color_invalid(self):
        """Test validation of invalid colors."""
        from docx_mcp.models.formatting import validate_color
        
        assert validate_color("invalid") is False
        assert validate_color("FF00") is False  # Too short
        assert validate_color("FF0000FF") is False  # Too long
        assert validate_color("GGGGGG") is False  # Invalid hex
    
    def test_hex_to_rgb(self):
        """Test hex to RGB conversion."""
        from docx_mcp.models.formatting import hex_to_rgb
        
        assert hex_to_rgb("FF0000") == (255, 0, 0)
        assert hex_to_rgb("00FF00") == (0, 255, 0)
        assert hex_to_rgb("0000FF") == (0, 0, 255)
        assert hex_to_rgb("#FFFFFF") == (255, 255, 255)
    
    def test_rgb_to_hex(self):
        """Test RGB to hex conversion."""
        from docx_mcp.models.formatting import rgb_to_hex
        
        assert rgb_to_hex(255, 0, 0) == "ff0000"
        assert rgb_to_hex(0, 255, 0) == "00ff00"
        assert rgb_to_hex(0, 0, 255) == "0000ff"
        assert rgb_to_hex(255, 255, 255) == "ffffff"
