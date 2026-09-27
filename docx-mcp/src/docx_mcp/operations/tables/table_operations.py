"""Table operations for Word documents."""

import re
from typing import List, Optional, Dict, Any, Union
from docx import Document
from docx.table import Table, _Cell
from docx.shared import Inches, Pt, RGBColor
from docx.oxml.shared import qn, OxmlElement
from docx.enum.text import WD_ALIGN_PARAGRAPH

from ...models.responses import OperationResponse, ResponseStatus
from ...models.tables import TableInfo, CellPosition, SearchResult, TableData, TableSearchMatch, TableSearchResult
from ...models.table_analysis import (
    TableStructureAnalysis, CellStyleAnalysis, TableAnalysisResult, MergeInfo,
    CellMergeType, analyze_cell_merge, extract_cell_formatting
)
from ...models.formatting import TextFormat, CellAlignment
from ...utils.exceptions import (
    TableNotFoundError,
    InvalidTableIndexError,
    InvalidCellPositionError,
    TableOperationError,
    DataFormatError,
    DocumentNotFoundError,
)
from ...utils.validation import (
    validate_table_index,
    validate_cell_position,
    validate_table_data,
    validate_position_parameter,
    sanitize_string,
)
from ...core.document_manager import DocumentManager
from .formatting import TableFormattingOperations


class TableOperations:
    """Handles table operations in Word documents."""
    
    def __init__(self, document_manager: DocumentManager):
        """
        Initialize table operations.
        
        Args:
            document_manager: Document manager instance
        """
        self.document_manager = document_manager
        self.formatting = TableFormattingOperations(document_manager)
    
    def create_table(
        self,
        file_path: str,
        rows: int,
        cols: int,
        position: str = "end",
        paragraph_index: Optional[int] = None,
        headers: Optional[List[str]] = None,
    ) -> OperationResponse:
        """
        Create a new table in the document.
        
        Args:
            file_path: Path to the document
            rows: Number of rows
            cols: Number of columns
            position: Where to insert the table
            paragraph_index: Paragraph index for 'after_paragraph' position
            headers: Optional header row data
            
        Returns:
            OperationResponse with operation result
        """
        try:
            # Validate inputs
            if rows <= 0 or cols <= 0:
                return OperationResponse.error("Rows and columns must be positive integers")
            
            valid_positions = ["end", "beginning", "after_paragraph"]
            validate_position_parameter(position, valid_positions)
            
            if position == "after_paragraph" and paragraph_index is None:
                return OperationResponse.error("paragraph_index required for 'after_paragraph' position")
            
            if headers and len(headers) != cols:
                return OperationResponse.error(f"Headers length ({len(headers)}) must match columns ({cols})")
            
            # Get document
            document = self.document_manager.get_or_load_document(file_path)
            
            # Create table
            table = None
            if position == "end":
                table = document.add_table(rows=rows, cols=cols)
            elif position == "beginning":
                # Insert at beginning by adding after title or first paragraph
                if document.paragraphs:
                    p = document.paragraphs[0]
                    table = p.insert_paragraph_before().add_table(rows=rows, cols=cols)
                else:
                    table = document.add_table(rows=rows, cols=cols)
            elif position == "after_paragraph":
                if paragraph_index < 0 or paragraph_index >= len(document.paragraphs):
                    return OperationResponse.error(f"Invalid paragraph index: {paragraph_index}")
                p = document.paragraphs[paragraph_index]
                new_p = p.insert_paragraph_after()
                table = new_p._element.addnext(document.add_table(rows=rows, cols=cols)._element)
                table = document.tables[-1]  # Get the newly added table
            
            if not table:
                return OperationResponse.error("Failed to create table")
            
            # Ensure the table uses Word's default inserted style (with borders)
            # Prefer the built-in "Table Grid" style; fall back gracefully if unavailable
            try:
                preferred_style_names = [
                    "Table Grid",      # common English name
                    "TableGrid",       # underlying styleId often used
                    "Normal Table"     # broader fallback present in most documents
                ]
                applied = False
                if hasattr(document, "styles"):
                    for style_name in preferred_style_names:
                        try:
                            style = document.styles[style_name]
                            if style and getattr(style, "type", None).__str__().lower().endswith("table"):
                                table.style = style
                                applied = True
                                break
                        except Exception:
                            continue
                if not applied:
                    # Last-resort attempt: set by name; Word may resolve localized names
                    table.style = "Table Grid"
            except Exception:
                # If styling fails, proceed without raising; Word will still render a usable table
                pass
            
            # Set headers if provided
            if headers:
                for col_idx, header in enumerate(headers):
                    table.cell(0, col_idx).text = sanitize_string(header)
            
            table_index = len(document.tables) - 1
            
            data = {
                "table_index": table_index,
                "rows": rows,
                "cols": cols,
                "position": position,
                "has_headers": bool(headers)
            }
            
            return OperationResponse.success(f"Table created with {rows} rows and {cols} columns", data)
            
        except Exception as e:
            return OperationResponse.error(f"Failed to create table: {str(e)}")
    
    def delete_table(self, file_path: str, table_index: int) -> OperationResponse:
        """
        Delete a table from the document.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table to delete
            
        Returns:
            OperationResponse with operation result
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            
            # Get table and remove it
            table = document.tables[table_index]
            table._element.getparent().remove(table._element)
            
            return OperationResponse.success(f"Table {table_index} deleted")
            
        except (InvalidTableIndexError, TableNotFoundError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to delete table: {str(e)}")
    
    def add_table_rows(
        self,
        file_path: str,
        table_index: int,
        count: int = 1,
        row_index: Optional[int] = None,
        copy_style_from_row: Optional[int] = None,
        default_text_format: Optional[TextFormat] = None,
        default_alignment: Optional[CellAlignment] = None,
        default_background_color: Optional[str] = None,
    ) -> OperationResponse:
        """
        Add rows to a table with optional styling control.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            count: Number of rows to add
            row_index: Insert position indicator.
                - If None: append to end
                - If -1: insert before the first row
                - If x >= 0: insert after row x
            copy_style_from_row: Row index to copy style from (None = no style copying)
            default_text_format: Default text formatting for new cells
            default_alignment: Default alignment for new cells
            default_background_color: Default background color for new cells
            
        Returns:
            OperationResponse with operation result
        """
        try:
            if count <= 0:
                return OperationResponse.error("Count must be a positive integer")

            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            # Validate row_index range if provided
            if row_index is not None:
                if row_index < -1 or (table.rows and row_index > len(table.rows) - 1):
                    return OperationResponse.error(f"Invalid row_index: {row_index}")
            
            # Determine reference row for style copying
            reference_row = None
            if copy_style_from_row is not None:
                # Explicit row specified
                if copy_style_from_row < 0 or copy_style_from_row >= len(table.rows):
                    return OperationResponse.error(f"Invalid copy_style_from_row: {copy_style_from_row}")
                reference_row = table.rows[copy_style_from_row]
            else:
                # Default behavior: choose a sensible reference based on insertion point
                if len(table.rows) > 0:
                    if row_index is None:
                        # Appending to end -> copy from last row
                        reference_row = table.rows[-1]
                    elif row_index == -1:
                        # Inserting before first -> copy from first row
                        reference_row = table.rows[0]
                    else:
                        # Inserting after row_index -> copy from that row
                        reference_row = table.rows[row_index]
            
            # Keep track of newly added rows for styling
            new_rows = []
            original_row_count = len(table.rows)
            
            # Add rows
            for i in range(count):
                new_row = table.add_row()
                # Compute target insertion index: after row_index => insert at row_index+1
                if row_index is None:
                    # Append to end: nothing to move
                    pass
                else:
                    insert_at = 0 if row_index == -1 else (row_index + 1)
                    # Account for previously inserted rows in this batch
                    insert_at += i
                    # If insert_at is within current rows, move before that index
                    if insert_at < len(table.rows) - 1:
                        target_row = table.rows[insert_at]
                        target_row._element.addprevious(new_row._element)
                new_rows.append(new_row)
            
            # Apply styling to new rows
            self._apply_row_styling(
                new_rows, 
                reference_row, 
                default_text_format, 
                default_alignment, 
                default_background_color
            )
            
            data = {
                "table_index": table_index,
                "rows_added": count,
                "new_row_count": len(table.rows),
                "insert_after_row_index": row_index,
            }
            
            return OperationResponse.success(f"Added {count} rows to table {table_index}", data)
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to add rows: {str(e)}")
    
    def add_table_columns(
        self,
        file_path: str,
        table_index: int,
        count: int = 1,
        column_index: Optional[int] = None,
        copy_style_from_column: Optional[int] = None,
        default_text_format: Optional[TextFormat] = None,
        default_alignment: Optional[CellAlignment] = None,
        default_background_color: Optional[str] = None,
    ) -> OperationResponse:
        """
        Add columns to a table with optional styling control.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            count: Number of columns to add
            column_index: Insert position indicator.
                - If None: append to end
                - If -1: insert before the first column
                - If x >= 0: insert after column x
            copy_style_from_column: Column index to copy style from (None = no style copying)
            default_text_format: Default text formatting for new cells
            default_alignment: Default alignment for new cells
            default_background_color: Default background color for new cells
            
        Returns:
            OperationResponse with operation result
        """
        try:
            if count <= 0:
                return OperationResponse.error("Count must be a positive integer")

            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            if not table.rows:
                return OperationResponse.error("Cannot add columns to empty table")
            
            original_cols = len(table.columns)
            
            # Validate column_index range if provided
            if column_index is not None:
                if column_index < -1 or column_index > original_cols - 1:
                    return OperationResponse.error(f"Invalid column_index: {column_index}")
            
            # Determine reference column for style copying
            reference_column = None
            if copy_style_from_column is not None:
                # Explicit column specified
                if copy_style_from_column < 0 or copy_style_from_column >= original_cols:
                    return OperationResponse.error(f"Invalid copy_style_from_column: {copy_style_from_column}")
                reference_column = [row.cells[copy_style_from_column] for row in table.rows]
            else:
                # Default behavior: choose a sensible reference based on insertion point
                if original_cols > 0:
                    if column_index is None:
                        # Appending to end -> copy from last column
                        reference_column = [row.cells[-1] for row in table.rows]
                    elif column_index == -1:
                        # Inserting before first -> copy from first column
                        reference_column = [row.cells[0] for row in table.rows]
                    else:
                        # Inserting after column_index -> copy from that column
                        reference_column = [row.cells[column_index] for row in table.rows]
            
            # Add columns using the proper python-docx method
            from docx.shared import Inches
            
            for i in range(count):
                table.add_column(width=Inches(1))
                if column_index is None:
                    # Appending to end: nothing to move
                    continue
                insert_at = 0 if column_index == -1 else (column_index + 1)
                insert_at += i
                # Move the last column to the specified position (before insert_at)
                if insert_at < len(table.columns) - 1:
                    for row in table.rows:
                        last_cell = row.cells[-1]
                        target_cell = row.cells[insert_at]
                        target_cell._element.addprevious(last_cell._element)
            
            # Apply styling to newly added columns
            if default_text_format or default_alignment or default_background_color or reference_column:
                self._apply_column_styling_after_add(
                    table, 
                    original_cols, 
                    count, 
                    column_index,
                    reference_column, 
                    default_text_format, 
                    default_alignment, 
                    default_background_color
                )
            
            new_cols = len(table.columns)
            
            data = {
                "table_index": table_index,
                "columns_added": count,
                "new_column_count": new_cols,
                "insert_after_column_index": column_index,
            }
            
            return OperationResponse.success(f"Added {count} columns to table {table_index}", data)
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to add columns: {str(e)}")
    
    def delete_table_rows(
        self, file_path: str, table_index: int, row_indices: List[int]
    ) -> OperationResponse:
        """
        Delete rows from a table.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            row_indices: List of row indices to delete
            
        Returns:
            OperationResponse with operation result
        """
        try:
            if not row_indices:
                return OperationResponse.error("No row indices provided")
            
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            # Validate all row indices
            for row_idx in row_indices:
                validate_cell_position(row_idx, 0, len(table.rows), len(table.columns))
            
            # Sort indices in reverse order to delete from end to beginning
            sorted_indices = sorted(set(row_indices), reverse=True)
            
            # Delete rows
            for row_idx in sorted_indices:
                row = table.rows[row_idx]
                row._element.getparent().remove(row._element)
            
            data = {
                "table_index": table_index,
                "rows_deleted": len(sorted_indices),
                "remaining_rows": len(table.rows)
            }
            
            return OperationResponse.success(
                f"Deleted {len(sorted_indices)} rows from table {table_index}", data
            )
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to delete rows: {str(e)}")
    
    def set_cell_value(
        self, 
        file_path: str, 
        table_index: int, 
        row_index: int, 
        column_index: int, 
        value: str,
        text_format: Optional[TextFormat] = None,
        alignment: Optional[Dict[str, str]] = None,
        background_color: Optional[str] = None,
        borders: Optional[Dict[str, Dict[str, str]]] = None,
        preserve_existing_format: bool = True,
        # Additional formatting parameters for convenience
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        underline: Optional[bool] = None,
        horizontal_alignment: Optional[str] = None,
        vertical_alignment: Optional[str] = None,
        # Border parameters
        top_style: Optional[str] = None,
        top_width: Optional[str] = None,
        top_color: Optional[str] = None,
        bottom_style: Optional[str] = None,
        bottom_width: Optional[str] = None,
        bottom_color: Optional[str] = None,
        left_style: Optional[str] = None,
        left_width: Optional[str] = None,
        left_color: Optional[str] = None,
        right_style: Optional[str] = None,
        right_width: Optional[str] = None,
        right_color: Optional[str] = None
    ) -> OperationResponse:
        """
        Set the value of a specific cell with optional formatting.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            row_index: Row index
            column_index: Column index
            value: Value to set
            text_format: Optional text formatting (font, size, color, bold, italic, etc.)
            alignment: Optional alignment settings {"horizontal": "left/center/right", "vertical": "top/middle/bottom"}
            background_color: Optional background color as hex string (e.g., "FFFF00")
            borders: Optional border settings {"top/bottom/left/right": {"style": "solid", "width": "thin", "color": "000000"}}
            preserve_existing_format: Whether to preserve existing formatting when not specified
            # Convenience parameters (will override text_format, alignment, borders if provided)
            font_family: Optional font family (e.g., "Arial", "Times New Roman")
            font_size: Optional font size in points (8-72)
            font_color: Optional font color as hex string (e.g., "FF0000" for red)
            bold: Optional bold formatting
            italic: Optional italic formatting
            underline: Optional underline formatting
            horizontal_alignment: Optional horizontal alignment ("left", "center", "right", "justify")
            vertical_alignment: Optional vertical alignment ("top", "middle", "bottom")
            # Border convenience parameters
            top_style: Optional top border style ("solid", "dashed", "dotted", "double", "none")
            top_width: Optional top border width ("thin", "medium", "thick")
            top_color: Optional top border color as hex string
            bottom_style: Optional bottom border style
            bottom_width: Optional bottom border width
            bottom_color: Optional bottom border color as hex string
            left_style: Optional left border style
            left_width: Optional left border width
            left_color: Optional left border color as hex string
            right_style: Optional right border style
            right_width: Optional right border width
            right_color: Optional right border color as hex string
            
        Returns:
            OperationResponse with operation result
        """
        try:
            from docx.shared import RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            
            # Process convenience parameters - they override the structured parameters
            if any([font_family, font_size, font_color, bold is not None, italic is not None, underline is not None]):
                text_format = TextFormat(
                    font_family=font_family,
                    font_size=font_size,
                    font_color=font_color,
                    bold=bold,
                    italic=italic,
                    underline=underline
                )
            
            if horizontal_alignment or vertical_alignment:
                alignment = {}
                if horizontal_alignment:
                    alignment["horizontal"] = horizontal_alignment
                if vertical_alignment:
                    alignment["vertical"] = vertical_alignment
            
            if any([top_style, top_width, top_color, bottom_style, bottom_width, bottom_color,
                    left_style, left_width, left_color, right_style, right_width, right_color]):
                borders = {}
                
                if any([top_style, top_width, top_color]):
                    borders["top"] = {
                        "style": top_style or "solid",
                        "width": top_width or "thin",
                        "color": top_color or "000000"
                    }
                
                if any([bottom_style, bottom_width, bottom_color]):
                    borders["bottom"] = {
                        "style": bottom_style or "solid",
                        "width": bottom_width or "thin",
                        "color": bottom_color or "000000"
                    }
                
                if any([left_style, left_width, left_color]):
                    borders["left"] = {
                        "style": left_style or "solid",
                        "width": left_width or "thin",
                        "color": left_color or "000000"
                    }
                
                if any([right_style, right_width, right_color]):
                    borders["right"] = {
                        "style": right_style or "solid",
                        "width": right_width or "thin",
                        "color": right_color or "000000"
                    }
            
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            validate_cell_position(row_index, column_index, len(table.rows), len(table.columns))
            
            # Get cell and set value
            cell = table.cell(row_index, column_index)
            
            # Store existing formatting if preserve_existing_format is True
            existing_format = None
            if preserve_existing_format:
                existing_format = extract_cell_formatting(cell)
            
            # Clear existing content and set new value
            cell.text = sanitize_string(value)
            
            # Apply formatting if provided
            if cell.paragraphs:
                paragraph = cell.paragraphs[0]
                
                # Apply paragraph alignment
                if alignment and alignment.get('horizontal'):
                    h_align = alignment['horizontal'].lower()
                    alignment_map = {
                        'left': WD_ALIGN_PARAGRAPH.LEFT,
                        'center': WD_ALIGN_PARAGRAPH.CENTER,
                        'right': WD_ALIGN_PARAGRAPH.RIGHT,
                        'justify': WD_ALIGN_PARAGRAPH.JUSTIFY
                    }
                    if h_align in alignment_map:
                        paragraph.alignment = alignment_map[h_align]
                elif preserve_existing_format and existing_format and existing_format.get('horizontal_alignment'):
                    # Restore existing alignment
                    h_align = existing_format['horizontal_alignment']
                    alignment_map = {
                        'left': WD_ALIGN_PARAGRAPH.LEFT,
                        'center': WD_ALIGN_PARAGRAPH.CENTER,
                        'right': WD_ALIGN_PARAGRAPH.RIGHT,
                        'justify': WD_ALIGN_PARAGRAPH.JUSTIFY
                    }
                    if h_align in alignment_map:
                        paragraph.alignment = alignment_map[h_align]
                
                # Apply text formatting to runs
                if paragraph.runs:
                    run = paragraph.runs[0]
                    
                    # Apply text formatting
                    if preserve_existing_format and existing_format:
                        # First restore existing text formatting
                        if existing_format.get('font_family'):
                            run.font.name = existing_format['font_family']
                        if existing_format.get('font_size'):
                            from docx.shared import Pt
                            run.font.size = Pt(existing_format['font_size'])
                        if existing_format.get('font_color'):
                            try:
                                color_hex = existing_format['font_color'].lstrip('#')
                                if len(color_hex) == 6:
                                    r = int(color_hex[0:2], 16)
                                    g = int(color_hex[2:4], 16)
                                    b = int(color_hex[4:6], 16)
                                    run.font.color.rgb = RGBColor(r, g, b)
                            except (ValueError, AttributeError):
                                pass
                        if existing_format.get('is_bold') is not None:
                            run.font.bold = existing_format['is_bold']
                        if existing_format.get('is_italic') is not None:
                            run.font.italic = existing_format['is_italic']
                        if existing_format.get('is_underlined') is not None:
                            run.font.underline = existing_format['is_underlined']
                    
                    # Then apply new text formatting (overrides existing)
                    if text_format:
                        if text_format.font_family:
                            run.font.name = text_format.font_family
                        if text_format.font_size:
                            from docx.shared import Pt
                            run.font.size = Pt(text_format.font_size)
                        if text_format.font_color:
                            # Parse hex color
                            try:
                                color_hex = text_format.font_color.lstrip('#')
                                if len(color_hex) == 6:
                                    r = int(color_hex[0:2], 16)
                                    g = int(color_hex[2:4], 16)
                                    b = int(color_hex[4:6], 16)
                                    run.font.color.rgb = RGBColor(r, g, b)
                            except (ValueError, AttributeError):
                                pass  # Skip invalid color
                        if text_format.bold is not None:
                            run.font.bold = text_format.bold
                        if text_format.italic is not None:
                            run.font.italic = text_format.italic
                        if text_format.underline is not None:
                            run.font.underline = text_format.underline
            
            # Apply vertical alignment if provided
            if alignment and alignment.get('vertical'):
                try:
                    from docx.oxml.shared import qn, OxmlElement
                    
                    v_align = alignment['vertical'].lower()
                    alignment_map = {
                        'top': 'top',
                        'middle': 'center',
                        'bottom': 'bottom'
                    }
                    
                    if v_align in alignment_map:
                        tc_pr = cell._element.get_or_add_tcPr()
                        
                        # Remove existing vAlign if present
                        existing_valign = tc_pr.find(qn('w:vAlign'))
                        if existing_valign is not None:
                            tc_pr.remove(existing_valign)
                        
                        # Add new vAlign
                        valign_element = OxmlElement('w:vAlign')
                        valign_element.set(qn('w:val'), alignment_map[v_align])
                        tc_pr.append(valign_element)
                except Exception:
                    pass  # Skip if vertical alignment application fails
            elif preserve_existing_format and existing_format and existing_format.get('vertical_alignment'):
                # Restore existing vertical alignment
                try:
                    from docx.oxml.shared import qn, OxmlElement
                    
                    v_align = existing_format['vertical_alignment'].lower()
                    alignment_map = {
                        'top': 'top',
                        'middle': 'center',
                        'bottom': 'bottom'
                    }
                    
                    if v_align in alignment_map:
                        tc_pr = cell._element.get_or_add_tcPr()
                        
                        # Remove existing vAlign if present
                        existing_valign = tc_pr.find(qn('w:vAlign'))
                        if existing_valign is not None:
                            tc_pr.remove(existing_valign)
                        
                        # Add new vAlign
                        valign_element = OxmlElement('w:vAlign')
                        valign_element.set(qn('w:val'), alignment_map[v_align])
                        tc_pr.append(valign_element)
                except Exception:
                    pass
            
            # Apply background color if provided
            if background_color:
                try:
                    # Apply cell shading using proper XML construction
                    from docx.oxml.shared import qn
                    from docx.oxml import parse_xml
                    
                    tc_pr = cell._element.get_or_add_tcPr()
                    
                    # Remove existing shading if present
                    existing_shd = tc_pr.find(qn('w:shd'))
                    if existing_shd is not None:
                        tc_pr.remove(existing_shd)
                    
                    # Create new shading element with proper namespace
                    shd_xml = f'''<w:shd xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" 
                                 w:val="clear" w:color="auto" w:fill="{background_color.lstrip('#')}"/>'''
                    shd_element = parse_xml(shd_xml)
                    tc_pr.append(shd_element)
                except Exception:
                    pass  # Skip if background color application fails
            elif preserve_existing_format and existing_format and existing_format.get('background_color'):
                # Restore existing background color
                try:
                    from docx.oxml.shared import qn
                    from docx.oxml import parse_xml
                    
                    tc_pr = cell._element.get_or_add_tcPr()
                    
                    # Remove existing shading if present
                    existing_shd = tc_pr.find(qn('w:shd'))
                    if existing_shd is not None:
                        tc_pr.remove(existing_shd)
                    
                    # Create new shading element with proper namespace
                    shd_xml = f'''<w:shd xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" 
                                 w:val="clear" w:color="auto" w:fill="{existing_format["background_color"].lstrip('#')}"/>'''
                    shd_element = parse_xml(shd_xml)
                    tc_pr.append(shd_element)
                except Exception:
                    pass
            
            # Apply borders if provided
            if borders:
                try:
                    from docx.oxml.shared import qn
                    from docx.oxml import parse_xml
                    
                    tc_pr = cell._element.get_or_add_tcPr()
                    
                    # Remove existing borders if present
                    existing_borders = tc_pr.find(qn('w:tcBorders'))
                    if existing_borders is not None:
                        tc_pr.remove(existing_borders)
                    
                    # Create new borders element
                    borders_xml = '<w:tcBorders xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    
                    for side, border_props in borders.items():
                        style = border_props.get('style', 'solid')
                        width = border_props.get('width', 'thin')
                        color = border_props.get('color', '000000').lstrip('#')
                        
                        # Map style names to Word constants
                        style_map = {
                            'solid': 'single',
                            'dashed': 'dashed',
                            'dotted': 'dotted',
                            'double': 'double',
                            'none': 'none'
                        }
                        
                        # Map width names to Word constants
                        width_map = {
                            'thin': '4',
                            'medium': '12',
                            'thick': '24'
                        }
                        
                        word_style = style_map.get(style, 'single')
                        word_width = width_map.get(width, '4')
                        
                        borders_xml += f'<w:{side} w:val="{word_style}" w:sz="{word_width}" w:color="{color}"/>'
                    
                    borders_xml += '</w:tcBorders>'
                    borders_element = parse_xml(borders_xml)
                    tc_pr.append(borders_element)
                except Exception:
                    pass  # Skip if border application fails
            
            # Get final formatting for response
            final_format = extract_cell_formatting(cell)
            
            data = {
                "table_index": table_index,
                "row_index": row_index,
                "column_index": column_index,
                "value": cell.text,
                "applied_formatting": {
                    "text_format": {
                        "font_family": final_format.get('font_family'),
                        "font_size": final_format.get('font_size'),
                        "font_color": final_format.get('font_color'),
                        "bold": final_format.get('is_bold', False),
                        "italic": final_format.get('is_italic', False),
                        "underlined": final_format.get('is_underlined', False)
                    },
                    "alignment": {
                        "horizontal": final_format.get('horizontal_alignment'),
                        "vertical": final_format.get('vertical_alignment')
                    },
                    "background_color": final_format.get('background_color')
                }
            }
            
            return OperationResponse.success(
                f"Cell value and formatting set at table {table_index}, row {row_index}, column {column_index}",
                data
            )
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to set cell value: {str(e)}")
    
    def set_multiple_cells(
        self,
        file_path: str,
        table_index: int,
        cells: List[Dict[str, Any]],
        preserve_existing_format: bool = True
    ) -> OperationResponse:
        """
        Set values and formatting for multiple cells in a table.
        
        This is a batch operation that allows setting multiple cells at once,
        which is more efficient than calling set_cell_value multiple times.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            cells: List of cell data dictionaries
            preserve_existing_format: Whether to preserve existing formatting when not specified
            
        Returns:
            OperationResponse with operation result
        """
        try:
            if not cells:
                return OperationResponse.error("No cells provided")
            
            document = self.document_manager.get_or_load_document(file_path)
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            results = []
            errors = []
            
            for i, cell_data in enumerate(cells):
                try:
                    # Extract required fields
                    row_index = cell_data.get('row_index')
                    column_index = cell_data.get('column_index')
                    value = cell_data.get('value', '')
                    
                    if row_index is None or column_index is None:
                        errors.append(f"Cell {i}: Missing row_index or column_index")
                        continue
                    
                    # Validate cell position
                    validate_cell_position(row_index, column_index, len(table.rows), len(table.columns))
                    
                    # Extract formatting parameters
                    font_family = cell_data.get('font_family')
                    font_size = cell_data.get('font_size')
                    font_color = cell_data.get('font_color')
                    bold = cell_data.get('bold')
                    italic = cell_data.get('italic')
                    underline = cell_data.get('underline')
                    horizontal_alignment = cell_data.get('horizontal_alignment')
                    vertical_alignment = cell_data.get('vertical_alignment')
                    background_color = cell_data.get('background_color')
                    
                    # Extract border parameters
                    top_style = cell_data.get('top_style')
                    top_width = cell_data.get('top_width')
                    top_color = cell_data.get('top_color')
                    bottom_style = cell_data.get('bottom_style')
                    bottom_width = cell_data.get('bottom_width')
                    bottom_color = cell_data.get('bottom_color')
                    left_style = cell_data.get('left_style')
                    left_width = cell_data.get('left_width')
                    left_color = cell_data.get('left_color')
                    right_style = cell_data.get('right_style')
                    right_width = cell_data.get('right_width')
                    right_color = cell_data.get('right_color')
                    
                    # Build text format if any text formatting is specified
                    text_format = None
                    if any([font_family, font_size, font_color, bold is not None, italic is not None, underline is not None]):
                        text_format = TextFormat(
                            font_family=font_family,
                            font_size=font_size,
                            font_color=font_color,
                            bold=bold,
                            italic=italic,
                            underline=underline
                        )
                    
                    # Build alignment if any alignment is specified
                    alignment = None
                    if horizontal_alignment or vertical_alignment:
                        alignment = {}
                        if horizontal_alignment:
                            alignment["horizontal"] = horizontal_alignment
                        if vertical_alignment:
                            alignment["vertical"] = vertical_alignment
                    
                    # Build borders if any border parameters are specified
                    borders = None
                    if any([top_style, top_width, top_color, bottom_style, bottom_width, bottom_color,
                            left_style, left_width, left_color, right_style, right_width, right_color]):
                        borders = {}
                        
                        if any([top_style, top_width, top_color]):
                            borders["top"] = {
                                "style": top_style or "solid",
                                "width": top_width or "thin",
                                "color": top_color or "000000"
                            }
                        
                        if any([bottom_style, bottom_width, bottom_color]):
                            borders["bottom"] = {
                                "style": bottom_style or "solid",
                                "width": bottom_width or "thin",
                                "color": bottom_color or "000000"
                            }
                        
                        if any([left_style, left_width, left_color]):
                            borders["left"] = {
                                "style": left_style or "solid",
                                "width": left_width or "thin",
                                "color": left_color or "000000"
                            }
                        
                        if any([right_style, right_width, right_color]):
                            borders["right"] = {
                                "style": right_style or "solid",
                                "width": right_width or "thin",
                                "color": right_color or "000000"
                            }
                    
                    # Set the cell value using existing method
                    cell_result = self.set_cell_value(
                        file_path,
                        table_index,
                        row_index,
                        column_index,
                        value,
                        text_format=text_format,
                        alignment=alignment,
                        background_color=background_color,
                        borders=borders,
                        preserve_existing_format=preserve_existing_format
                    )
                    
                    if cell_result.status == ResponseStatus.SUCCESS:
                        results.append({
                            "row_index": row_index,
                            "column_index": column_index,
                            "value": value,
                            "status": "success"
                        })
                    else:
                        errors.append(f"Cell {i} (row {row_index}, col {column_index}): {cell_result.message}")
                        
                except (InvalidTableIndexError, InvalidCellPositionError) as e:
                    errors.append(f"Cell {i}: {str(e)}")
                except Exception as e:
                    errors.append(f"Cell {i}: {str(e)}")
            
            # Prepare response data
            data = {
                "table_index": table_index,
                "total_cells": len(cells),
                "successful_cells": len(results),
                "failed_cells": len(errors),
                "results": results,
                "errors": errors
            }
            
            if errors:
                if results:
                    return OperationResponse.success(
                        f"Batch operation completed with {len(results)} successes and {len(errors)} failures",
                        data
                    )
                else:
                    return OperationResponse.error(
                        f"All {len(cells)} cells failed to update",
                        data
                    )
            else:
                return OperationResponse.success(
                    f"Successfully updated {len(results)} cells",
                    data
                )
                
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to set multiple cells: {str(e)}")
    
    def get_cell_value(
        self, 
        file_path: str, 
        table_index: int, 
        row_index: int, 
        column_index: int,
        include_formatting: bool = True
    ) -> OperationResponse:
        """
        Get the value and formatting of a specific cell.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            row_index: Row index
            column_index: Column index
            include_formatting: Whether to include detailed formatting information
            
        Returns:
            OperationResponse with cell value and formatting
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            validate_cell_position(row_index, column_index, len(table.rows), len(table.columns))
            
            # Get cell and its value
            cell = table.cell(row_index, column_index)
            value = cell.text
            
            data = {
                "table_index": table_index,
                "row_index": row_index,
                "column_index": column_index,
                "value": value,
                "is_empty": not value.strip()
            }
            
            # Include formatting information if requested
            if include_formatting:
                cell_format = extract_cell_formatting(cell)
                merge_info = analyze_cell_merge(cell, row_index, column_index)
                
                data["formatting"] = {
                    "text_format": {
                        "font_family": cell_format.get('font_family'),
                        "font_size": cell_format.get('font_size'),
                        "font_color": cell_format.get('font_color'),
                        "bold": cell_format.get('is_bold', False),
                        "italic": cell_format.get('is_italic', False),
                        "underlined": cell_format.get('is_underlined', False),
                        "strikethrough": cell_format.get('is_strikethrough', False)
                    },
                    "alignment": {
                        "horizontal": cell_format.get('horizontal_alignment'),
                        "vertical": cell_format.get('vertical_alignment')
                    },
                    "background_color": cell_format.get('background_color'),
                    "borders": self._extract_border_data(cell_format.get('borders', {}))
                }
                
                # Include merge information if cell is merged
                if merge_info:
                    data["merge_info"] = {
                        "type": merge_info.merge_type.value,
                        "start_row": merge_info.start_row,
                        "end_row": merge_info.end_row,
                        "start_col": merge_info.start_col,
                        "end_col": merge_info.end_col,
                        "span_rows": merge_info.span_rows,
                        "span_cols": merge_info.span_cols
                    }
                else:
                    data["merge_info"] = None
            
            message = "Cell value retrieved"
            if include_formatting:
                message += " with formatting"
            
            return OperationResponse.success(message, data)
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to get cell value: {str(e)}")
    
    def get_table_data_and_structure(
        self,
        file_path: str,
        table_index: int,
        start_row: int = 0,
        end_row: Optional[int] = None,
        start_col: int = 0,
        end_col: Optional[int] = None,
        include_headers: bool = True,
        format_type: str = "array",
    ) -> OperationResponse:
        """
        Get table data and structure information within specified range.
        
        This interface returns table content, merge information, and basic structure
        without detailed cell formatting to keep response size manageable.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            start_row: Starting row index (0-based, inclusive)
            end_row: Ending row index (0-based, exclusive). None means to the end
            start_col: Starting column index (0-based, inclusive)
            end_col: Ending column index (0-based, exclusive). None means to the end
            include_headers: Whether to include headers
            format_type: Format of returned data ('array', 'object', 'csv')
            
        Returns:
            OperationResponse with table data and structure
        """
        try:
            valid_formats = ["array", "object", "csv"]
            if format_type not in valid_formats:
                return OperationResponse.error(f"Invalid format. Valid options: {', '.join(valid_formats)}")
            
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            if not table.rows:
                return OperationResponse.success("Table is empty", {
                    "table_index": table_index,
                    "format": format_type,
                    "rows": 0,
                    "columns": 0,
                    "has_headers": False,
                    "headers": None,
                    "data": [],
                    "merge_regions": [],
                    "range_info": {
                        "total_rows": 0,
                        "total_columns": 0,
                        "requested_range": {
                            "start_row": start_row,
                            "end_row": end_row,
                            "start_col": start_col,
                            "end_col": end_col
                        },
                        "rows_returned": 0,
                        "columns_returned": 0,
                        "has_more_rows": False,
                        "has_more_cols": False
                    }
                })
            
            total_rows = len(table.rows)
            total_cols = len(table.columns) if table.rows else 0
            
            # Validate and adjust row range
            if start_row < 0:
                start_row = 0
            if start_row >= total_rows:
                return OperationResponse.error(f"start_row ({start_row}) exceeds table rows ({total_rows})")
            
            if end_row is None:
                end_row = total_rows
            elif end_row > total_rows:
                end_row = total_rows
            elif end_row <= start_row:
                return OperationResponse.error("end_row must be greater than start_row")
            
            # Validate and adjust column range
            if start_col < 0:
                start_col = 0
            if start_col >= total_cols:
                return OperationResponse.error(f"start_col ({start_col}) exceeds table columns ({total_cols})")
            
            if end_col is None:
                end_col = total_cols
            elif end_col > total_cols:
                end_col = total_cols
            elif end_col <= start_col:
                return OperationResponse.error("end_col must be greater than start_col")
            
            # Extract headers if requested
            headers = None
            data_start_row = start_row
            if include_headers and table.rows and start_row == 0:
                header_cells = [cell.text for cell in table.rows[0].cells[start_col:end_col]]
                # Only treat as headers if at least one cell has non-empty content
                if any(cell.strip() for cell in header_cells):
                    headers = header_cells
                    data_start_row = 1
                    # Adjust end_row if we're including headers
                    if end_row > 1:
                        end_row = max(1, end_row)
            
            # Extract data rows
            data = []
            merge_regions = []
            
            for row_idx in range(data_start_row, end_row):
                row = table.rows[row_idx]
                row_data = []
                
                for col_idx in range(start_col, end_col):
                    if col_idx < len(row.cells):
                        cell = row.cells[col_idx]
                        cell_text = cell.text
                        row_data.append(cell_text)
                        
                        # Check for merge information
                        merge_info = analyze_cell_merge(cell, row_idx, col_idx)
                        if merge_info and merge_info.merge_type != CellMergeType.NONE:
                            # Adjust merge coordinates to relative range
                            relative_merge = {
                                "type": merge_info.merge_type.value,
                                "start_row": max(0, merge_info.start_row - start_row),
                                "end_row": min(end_row - start_row - 1, merge_info.end_row - start_row),
                                "start_col": max(0, merge_info.start_col - start_col),
                                "end_col": min(end_col - start_col - 1, merge_info.end_col - start_col),
                                "span_rows": merge_info.span_rows,
                                "span_cols": merge_info.span_cols,
                                "absolute_position": {
                                    "start_row": merge_info.start_row,
                                    "end_row": merge_info.end_row,
                                    "start_col": merge_info.start_col,
                                    "end_col": merge_info.end_col
                                }
                            }
                            merge_regions.append(relative_merge)
                    else:
                        row_data.append("")
                
                data.append(row_data)
            
            # Format data according to requested format
            if format_type == "array":
                result_data = data
                if include_headers and headers and start_row == 0:
                    result_data = [headers] + data
            elif format_type == "object":
                if headers:
                    result_data = []
                    for row in data:
                        row_dict = {}
                        for i, value in enumerate(row):
                            header = headers[i] if i < len(headers) else f"Column_{i + start_col}"
                            row_dict[header] = value
                        result_data.append(row_dict)
                else:
                    result_data = [{"Column_" + str(i + start_col): value for i, value in enumerate(row)} for row in data]
            elif format_type == "csv":
                result_data = []
                if include_headers and headers and start_row == 0:
                    result_data.append(headers)
                result_data.extend(data)
            
            # Calculate range info
            rows_returned = len(data)
            if include_headers and headers and start_row == 0:
                rows_returned += 1
            
            range_info = {
                "total_rows": total_rows,
                "total_columns": total_cols,
                "requested_range": {
                    "start_row": start_row,
                    "end_row": end_row,
                    "start_col": start_col,
                    "end_col": end_col
                },
                "rows_returned": rows_returned,
                "columns_returned": end_col - start_col,
                "has_more_rows": end_row < total_rows,
                "has_more_cols": end_col < total_cols
            }
            
            response_data = {
                "table_index": table_index,
                "format": format_type,
                "rows": len(data),
                "columns": len(data[0]) if data else 0,
                "has_headers": bool(headers),
                "headers": headers,
                "data": result_data,
                "merge_regions": merge_regions,
                "range_info": range_info
            }
            
            return OperationResponse.success(
                f"Table data and structure retrieved in {format_type} format (rows {start_row}-{end_row-1}, cols {start_col}-{end_col-1})", 
                response_data
            )
            
        except (InvalidTableIndexError,) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to get table data and structure: {str(e)}")

    def get_table_styles(
        self,
        file_path: str,
        table_index: int,
        start_row: int = 0,
        end_row: Optional[int] = None,
        start_col: int = 0,
        end_col: Optional[int] = None,
    ) -> OperationResponse:
        """
        Get table cell styles and formatting information within specified range.
        
        This interface returns detailed cell formatting information including
        fonts, colors, alignment, borders, and background colors.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            start_row: Starting row index (0-based, inclusive)
            end_row: Ending row index (0-based, exclusive). None means to the end
            start_col: Starting column index (0-based, inclusive)
            end_col: Ending column index (0-based, exclusive). None means to the end
            
        Returns:
            OperationResponse with table cell styles
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            if not table.rows:
                return OperationResponse.success("Table is empty", {"styles": []})
            
            total_rows = len(table.rows)
            total_cols = len(table.columns) if table.rows else 0
            
            # Validate and adjust row range
            if start_row < 0:
                start_row = 0
            if start_row >= total_rows:
                return OperationResponse.error(f"start_row ({start_row}) exceeds table rows ({total_rows})")
            
            if end_row is None:
                end_row = total_rows
            elif end_row > total_rows:
                end_row = total_rows
            elif end_row <= start_row:
                return OperationResponse.error("end_row must be greater than start_row")
            
            # Validate and adjust column range
            if start_col < 0:
                start_col = 0
            if start_col >= total_cols:
                return OperationResponse.error(f"start_col ({start_col}) exceeds table columns ({total_cols})")
            
            if end_col is None:
                end_col = total_cols
            elif end_col > total_cols:
                end_col = total_cols
            elif end_col <= start_col:
                return OperationResponse.error("end_col must be greater than start_col")
            
            # Extract cell styles
            cell_styles = []
            style_summary = {
                "font_families": set(),
                "font_sizes": set(),
                "colors": set(),
                "background_colors": set(),
                "alignments": set(),
                "border_styles": set()
            }
            
            for row_idx in range(start_row, end_row):
                row = table.rows[row_idx]
                row_styles = []
                
                for col_idx in range(start_col, end_col):
                    if col_idx < len(row.cells):
                        cell = row.cells[col_idx]
                        
                        # Extract comprehensive formatting
                        formatting = extract_cell_formatting(cell)
                        merge_info = analyze_cell_merge(cell, row_idx, col_idx)
                        
                        # Build cell style object
                        cell_style = {
                            "position": {
                                "row": row_idx,
                                "column": col_idx,
                                "relative_row": row_idx - start_row,
                                "relative_column": col_idx - start_col
                            },
                            "text_format": {
                                "font_family": formatting["font_family"],
                                "font_size": formatting["font_size"],
                                "font_color": formatting["font_color"],
                                "bold": formatting["is_bold"],
                                "italic": formatting["is_italic"],
                                "underlined": formatting["is_underlined"],
                                "strikethrough": formatting["is_strikethrough"]
                            },
                            "alignment": {
                                "horizontal": formatting["horizontal_alignment"],
                                "vertical": formatting["vertical_alignment"]
                            },
                            "background": {
                                "color": formatting["background_color"]
                            },
                            "borders": {
                                "top": formatting["borders"]["top"],
                                "bottom": formatting["borders"]["bottom"],
                                "left": formatting["borders"]["left"],
                                "right": formatting["borders"]["right"]
                            }
                        }
                        
                        # Add merge information if present
                        if merge_info and merge_info.merge_type != CellMergeType.NONE:
                            cell_style["merge"] = {
                                "type": merge_info.merge_type.value,
                                "start_row": merge_info.start_row,
                                "end_row": merge_info.end_row,
                                "start_col": merge_info.start_col,
                                "end_col": merge_info.end_col,
                                "span_rows": merge_info.span_rows,
                                "span_cols": merge_info.span_cols
                            }
                        else:
                            cell_style["merge"] = None
                        
                        row_styles.append(cell_style)
                        
                        # Update style summary
                        if formatting["font_family"]:
                            style_summary["font_families"].add(formatting["font_family"])
                        if formatting["font_size"]:
                            style_summary["font_sizes"].add(formatting["font_size"])
                        if formatting["font_color"]:
                            style_summary["colors"].add(formatting["font_color"])
                        if formatting["background_color"]:
                            style_summary["background_colors"].add(formatting["background_color"])
                        if formatting["horizontal_alignment"]:
                            style_summary["alignments"].add(formatting["horizontal_alignment"])
                        
                        # Track border styles
                        for border_side, border_info in formatting["borders"].items():
                            if border_info and border_info.get("style"):
                                style_summary["border_styles"].add(border_info["style"])
                    else:
                        # Empty cell placeholder
                        row_styles.append({
                            "position": {
                                "row": row_idx,
                                "column": col_idx,
                                "relative_row": row_idx - start_row,
                                "relative_column": col_idx - start_col
                            },
                            "text_format": None,
                            "alignment": None,
                            "background": None,
                            "borders": None,
                            "merge": None
                        })
                
                cell_styles.append(row_styles)
            
            # Convert sets to lists for JSON serialization
            style_summary = {
                "font_families": list(style_summary["font_families"]),
                "font_sizes": list(style_summary["font_sizes"]),
                "colors": list(style_summary["colors"]),
                "background_colors": list(style_summary["background_colors"]),
                "alignments": list(style_summary["alignments"]),
                "border_styles": list(style_summary["border_styles"])
            }
            
            # Calculate range info
            range_info = {
                "total_rows": total_rows,
                "total_columns": total_cols,
                "requested_range": {
                    "start_row": start_row,
                    "end_row": end_row,
                    "start_col": start_col,
                    "end_col": end_col
                },
                "rows_returned": end_row - start_row,
                "columns_returned": end_col - start_col,
                "has_more_rows": end_row < total_rows,
                "has_more_cols": end_col < total_cols
            }
            
            response_data = {
                "table_index": table_index,
                "cell_styles": cell_styles,
                "style_summary": style_summary,
                "range_info": range_info
            }
            
            return OperationResponse.success(
                f"Table styles retrieved (rows {start_row}-{end_row-1}, cols {start_col}-{end_col-1})", 
                response_data
            )
            
        except (InvalidTableIndexError,) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to get table styles: {str(e)}")

    def list_tables(self, file_path: str, include_summary: bool = True) -> OperationResponse:
        """
        List all tables in the document.
        
        Args:
            file_path: Path to the document
            include_summary: Whether to include table summary information
            
        Returns:
            OperationResponse with list of tables
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            tables = []
            for i, table in enumerate(document.tables):
                table_info = {
                    "index": i,
                    "rows": len(table.rows),
                    "columns": len(table.columns) if table.rows else 0,
                }
                
                if include_summary:
                    # Check if has headers (simple heuristic)
                    has_headers = False
                    if table.rows:
                        first_row_has_text = all(cell.text.strip() for cell in table.rows[0].cells)
                        has_headers = first_row_has_text
                    
                    table_info.update({
                        "has_headers": has_headers,
                        "style": getattr(table.style, 'name', None) if table.style else None,
                        "first_row_data": [cell.text for cell in table.rows[0].cells] if table.rows else []
                    })
                
                tables.append(table_info)
            
            data = {
                "tables": tables,
                "total_count": len(tables)
            }
            
            return OperationResponse.success(f"Found {len(tables)} tables", data)
            
        except Exception as e:
            return OperationResponse.error(f"Failed to list tables: {str(e)}")
    
    def search_table_content(
        self,
        file_path: str,
        query: str,
        search_mode: str = "contains",
        case_sensitive: bool = False,
        table_indices: Optional[List[int]] = None,
        max_results: Optional[int] = None
    ) -> OperationResponse:
        """
        Search for content within table cells.
        
        Args:
            file_path: Path to the document
            query: Search query string
            search_mode: Search mode ("exact", "contains", "regex")
            case_sensitive: Whether search is case sensitive
            table_indices: Optional list of table indices to search (None = all tables)
            max_results: Maximum number of results to return (None = no limit)
            
        Returns:
            OperationResponse with search results
        """
        try:
            if not query.strip():
                return OperationResponse.error("Search query cannot be empty")
            
            valid_modes = ["exact", "contains", "regex"]
            if search_mode not in valid_modes:
                return OperationResponse.error(f"Invalid search mode. Valid options: {', '.join(valid_modes)}")
            
            document = self.document_manager.get_or_load_document(file_path)
            
            # Determine which tables to search
            if table_indices is None:
                tables_to_search = list(range(len(document.tables)))
            else:
                # Validate table indices
                for idx in table_indices:
                    validate_table_index(idx, len(document.tables))
                tables_to_search = table_indices
            
            matches = []
            summary = {
                "tables_with_matches": 0,
                "matches_per_table": {},
                "total_cells_searched": 0
            }
            
            # Compile regex pattern if needed
            pattern = None
            if search_mode == "regex":
                try:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    pattern = re.compile(query, flags)
                except re.error as e:
                    return OperationResponse.error(f"Invalid regex pattern: {str(e)}")
            
            # Search each table
            for table_idx in tables_to_search:
                table = document.tables[table_idx]
                table_matches = 0
                
                for row_idx, row in enumerate(table.rows):
                    for col_idx, cell in enumerate(row.cells):
                        cell_text = cell.text
                        summary["total_cells_searched"] += 1
                        
                        # Perform search based on mode
                        cell_matches = self._search_cell_content(
                            cell_text, query, search_mode, case_sensitive, pattern
                        )
                        
                        # Create match objects
                        for match_info in cell_matches:
                            if max_results and len(matches) >= max_results:
                                break
                                
                            match = TableSearchMatch(
                                table_index=table_idx,
                                row_index=row_idx,
                                column_index=col_idx,
                                cell_value=cell_text,
                                match_text=match_info["text"],
                                match_start=match_info["start"],
                                match_end=match_info["end"]
                            )
                            matches.append(match)
                            table_matches += 1
                        
                        if max_results and len(matches) >= max_results:
                            break
                    
                    if max_results and len(matches) >= max_results:
                        break
                
                if table_matches > 0:
                    summary["tables_with_matches"] += 1
                    summary["matches_per_table"][table_idx] = table_matches
            
            # Create search result
            search_result = TableSearchResult(
                query=query,
                search_mode=search_mode,
                case_sensitive=case_sensitive,
                matches=matches,
                total_matches=len(matches),
                tables_searched=tables_to_search,
                summary=summary
            )
            
            message = f"Found {len(matches)} matches in {summary['tables_with_matches']} tables"
            if max_results and len(matches) >= max_results:
                message += f" (limited to {max_results} results)"
            
            return OperationResponse.success(message, search_result.to_dict())
            
        except (InvalidTableIndexError,) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to search table content: {str(e)}")
    
    def _search_cell_content(
        self,
        cell_text: str,
        query: str,
        search_mode: str,
        case_sensitive: bool,
        pattern: Optional[re.Pattern] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for matches within a single cell's content.
        
        Args:
            cell_text: The cell's text content
            query: Search query
            search_mode: Search mode
            case_sensitive: Case sensitivity flag
            pattern: Compiled regex pattern (for regex mode)
            
        Returns:
            List of match information dictionaries
        """
        matches = []
        
        if not cell_text:
            return matches
        
        if search_mode == "exact":
            # Exact match
            search_text = cell_text if case_sensitive else cell_text.lower()
            query_text = query if case_sensitive else query.lower()
            
            if search_text == query_text:
                matches.append({
                    "text": cell_text,
                    "start": 0,
                    "end": len(cell_text)
                })
        
        elif search_mode == "contains":
            # Contains match
            search_text = cell_text if case_sensitive else cell_text.lower()
            query_text = query if case_sensitive else query.lower()
            
            start = 0
            while True:
                pos = search_text.find(query_text, start)
                if pos == -1:
                    break
                
                matches.append({
                    "text": cell_text[pos:pos + len(query)],
                    "start": pos,
                    "end": pos + len(query)
                })
                start = pos + 1
        
        elif search_mode == "regex":
            # Regex match
            if pattern:
                for match in pattern.finditer(cell_text):
                    matches.append({
                        "text": match.group(),
                        "start": match.start(),
                        "end": match.end()
                    })
        
        return matches
    
    def search_table_headers(
        self,
        file_path: str,
        query: str,
        search_mode: str = "contains",
        case_sensitive: bool = False
    ) -> OperationResponse:
        """
        Search specifically in table headers (first row of each table).
        
        Args:
            file_path: Path to the document
            query: Search query string
            search_mode: Search mode ("exact", "contains", "regex")
            case_sensitive: Whether search is case sensitive
            
        Returns:
            OperationResponse with search results
        """
        try:
            if not query.strip():
                return OperationResponse.error("Search query cannot be empty")
            
            document = self.document_manager.get_or_load_document(file_path)
            
            matches = []
            tables_with_headers = 0
            
            # Search only first row of each table
            for table_idx, table in enumerate(document.tables):
                if not table.rows:
                    continue
                
                first_row = table.rows[0]
                has_header_matches = False
                
                for col_idx, cell in enumerate(first_row.cells):
                    cell_text = cell.text
                    
                    # Use the same search logic as general search
                    pattern = None
                    if search_mode == "regex":
                        try:
                            flags = 0 if case_sensitive else re.IGNORECASE
                            pattern = re.compile(query, flags)
                        except re.error as e:
                            return OperationResponse.error(f"Invalid regex pattern: {str(e)}")
                    
                    cell_matches = self._search_cell_content(
                        cell_text, query, search_mode, case_sensitive, pattern
                    )
                    
                    for match_info in cell_matches:
                        match = TableSearchMatch(
                            table_index=table_idx,
                            row_index=0,  # Always first row for headers
                            column_index=col_idx,
                            cell_value=cell_text,
                            match_text=match_info["text"],
                            match_start=match_info["start"],
                            match_end=match_info["end"]
                        )
                        matches.append(match)
                        has_header_matches = True
                
                if has_header_matches:
                    tables_with_headers += 1
            
            # Create search result
            search_result = TableSearchResult(
                query=query,
                search_mode=search_mode,
                case_sensitive=case_sensitive,
                matches=matches,
                total_matches=len(matches),
                tables_searched=list(range(len(document.tables))),
                summary={
                    "search_type": "headers_only",
                    "tables_with_header_matches": tables_with_headers,
                    "total_tables": len(document.tables)
                }
            )
            
            message = f"Found {len(matches)} header matches in {tables_with_headers} tables"
            return OperationResponse.success(message, search_result.to_dict())
            
        except Exception as e:
            return OperationResponse.error(f"Failed to search table headers: {str(e)}")
    
    def analyze_table_structure(
        self,
        file_path: str,
        table_index: int,
        include_cell_details: bool = True
    ) -> OperationResponse:
        """
        Analyze the complete structure and styling of a specific table.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table to analyze
            include_cell_details: Whether to include detailed cell analysis
            
        Returns:
            OperationResponse with comprehensive table analysis
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            # Basic table information
            total_rows = len(table.rows)
            total_columns = len(table.columns) if table.rows else 0
            
            # Table-level properties
            table_style_name = getattr(table.style, 'name', None) if table.style else None
            
            # Header detection
            has_header_row = False
            header_row_index = None
            header_cells = None
            
            if table.rows:
                # Simple heuristic: if first row has text in all cells, consider it header
                first_row = table.rows[0]
                first_row_texts = [cell.text.strip() for cell in first_row.cells]
                has_header_row = all(text for text in first_row_texts)
                
                if has_header_row:
                    header_row_index = 0
                    header_cells = first_row_texts
            
            # Initialize cell analysis storage
            cells = []
            merge_regions = []
            merged_cells_count = 0
            
            # Style tracking for consistency analysis
            font_families = set()
            font_sizes = set()
            colors = set()
            background_colors = set()
            alignments = set()
            border_styles = set()
            
            # Analyze each cell
            for row_idx, row in enumerate(table.rows):
                cell_row = []
                for col_idx, cell in enumerate(row.cells):
                    # Extract cell content
                    text_content = cell.text
                    is_empty = not text_content.strip()
                    
                    # Analyze merge information
                    merge_info = analyze_cell_merge(cell, row_idx, col_idx)
                    if merge_info:
                        merge_regions.append(merge_info)
                        merged_cells_count += 1
                    
                    # Extract formatting if detailed analysis is requested
                    cell_analysis = None
                    if include_cell_details:
                        formatting = extract_cell_formatting(cell)
                        
                        # Track unique styles
                        if formatting["font_family"]:
                            font_families.add(formatting["font_family"])
                        if formatting["font_size"]:
                            font_sizes.add(formatting["font_size"])
                        if formatting["font_color"]:
                            colors.add(formatting["font_color"])
                        if formatting["background_color"]:
                            background_colors.add(formatting["background_color"])
                        if formatting["horizontal_alignment"]:
                            alignments.add(formatting["horizontal_alignment"])
                        
                        # Track border styles
                        for border_side, border_info in formatting["borders"].items():
                            if border_info and border_info.get("style"):
                                border_styles.add(border_info["style"])
                        
                        cell_analysis = CellStyleAnalysis(
                            row_index=row_idx,
                            column_index=col_idx,
                            text_content=text_content,
                            is_empty=is_empty,
                            merge_info=merge_info,
                            font_family=formatting["font_family"],
                            font_size=formatting["font_size"],
                            font_color=formatting["font_color"],
                            is_bold=formatting["is_bold"],
                            is_italic=formatting["is_italic"],
                            is_underlined=formatting["is_underlined"],
                            is_strikethrough=formatting["is_strikethrough"],
                            horizontal_alignment=formatting["horizontal_alignment"],
                            vertical_alignment=formatting["vertical_alignment"],
                            background_color=formatting["background_color"],
                            top_border=formatting["borders"]["top"],
                            bottom_border=formatting["borders"]["bottom"],
                            left_border=formatting["borders"]["left"],
                            right_border=formatting["borders"]["right"],
                            width=None,  # Could be implemented if needed
                            height=None  # Could be implemented if needed
                        )
                    else:
                        # Minimal cell analysis without formatting details
                        cell_analysis = CellStyleAnalysis(
                            row_index=row_idx,
                            column_index=col_idx,
                            text_content=text_content,
                            is_empty=is_empty,
                            merge_info=merge_info,
                            font_family=None,
                            font_size=None,
                            font_color=None,
                            is_bold=False,
                            is_italic=False,
                            is_underlined=False,
                            is_strikethrough=False,
                            horizontal_alignment=None,
                            vertical_alignment=None,
                            background_color=None,
                            top_border=None,
                            bottom_border=None,
                            left_border=None,
                            right_border=None,
                            width=None,
                            height=None
                        )
                    
                    cell_row.append(cell_analysis)
                
                cells.append(cell_row)
            
            # Style consistency analysis
            consistent_fonts = len(font_families) <= 1
            consistent_alignment = len(alignments) <= 1
            consistent_borders = len(border_styles) <= 1
            
            # Create table structure analysis
            table_analysis = TableStructureAnalysis(
                table_index=table_index,
                total_rows=total_rows,
                total_columns=total_columns,
                table_style_name=table_style_name,
                table_alignment=None,  # Could be implemented if needed
                table_width=None,      # Could be implemented if needed
                has_header_row=has_header_row,
                header_row_index=header_row_index,
                header_cells=header_cells,
                cells=cells,
                merged_cells_count=merged_cells_count,
                merge_regions=merge_regions,
                consistent_fonts=consistent_fonts,
                consistent_alignment=consistent_alignment,
                consistent_borders=consistent_borders,
                unique_font_families=list(font_families),
                unique_font_sizes=list(font_sizes),
                unique_colors=list(colors),
                unique_background_colors=list(background_colors)
            )
            
            return OperationResponse.success(
                f"Table {table_index} structure analyzed successfully",
                table_analysis.to_dict()
            )
            
        except DocumentNotFoundError:
            return OperationResponse.error("Document not loaded")
        except (InvalidTableIndexError,) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to analyze table structure: {str(e)}")
    
    def analyze_all_tables(
        self,
        file_path: str,
        include_cell_details: bool = True
    ) -> OperationResponse:
        """
        Analyze the structure and styling of all tables in the document.
        
        Args:
            file_path: Path to the document
            include_cell_details: Whether to include detailed cell analysis
            
        Returns:
            OperationResponse with analysis of all tables
        """
        try:
            from datetime import datetime
            
            document = self.document_manager.get_or_load_document(file_path)
            
            if not document.tables:
                return OperationResponse.success(
                    "No tables found in document",
                    {"file_path": file_path, "total_tables": 0, "tables": []}
                )
            
            table_analyses = []
            
            # Analyze each table
            for table_idx in range(len(document.tables)):
                analysis_response = self.analyze_table_structure(
                    file_path, table_idx, include_cell_details
                )
                
                if analysis_response.success:
                    # Extract the table analysis from the response data
                    table_data = analysis_response.data
                    table_analyses.append(TableStructureAnalysis(
                        table_index=table_data["table_info"]["index"],
                        total_rows=table_data["table_info"]["rows"],
                        total_columns=table_data["table_info"]["columns"],
                        table_style_name=table_data["table_info"]["style_name"],
                        table_alignment=table_data["table_info"]["alignment"],
                        table_width=table_data["table_info"]["width"],
                        has_header_row=table_data["header_info"]["has_header"],
                        header_row_index=table_data["header_info"]["header_row_index"],
                        header_cells=table_data["header_info"]["header_cells"],
                        cells=[],  # We'll populate this if needed
                        merged_cells_count=table_data["merge_analysis"]["merged_cells_count"],
                        merge_regions=[],  # We'll populate this if needed
                        consistent_fonts=table_data["style_consistency"]["fonts"],
                        consistent_alignment=table_data["style_consistency"]["alignment"],
                        consistent_borders=table_data["style_consistency"]["borders"],
                        unique_font_families=table_data["style_summary"]["font_families"],
                        unique_font_sizes=table_data["style_summary"]["font_sizes"],
                        unique_colors=table_data["style_summary"]["colors"],
                        unique_background_colors=table_data["style_summary"]["background_colors"]
                    ))
                else:
                    # If individual table analysis fails, log it but continue
                    continue
            
            # Create comprehensive analysis result
            analysis_result = TableAnalysisResult(
                file_path=file_path,
                total_tables=len(table_analyses),
                analysis_timestamp=datetime.now().isoformat(),
                tables=table_analyses
            )
            
            return OperationResponse.success(
                f"Analyzed {len(table_analyses)} tables successfully",
                analysis_result.to_dict()
            )
            
        except DocumentNotFoundError:
            return OperationResponse.error("Document not loaded")
        except Exception as e:
            return OperationResponse.error(f"Failed to analyze all tables: {str(e)}")
    
    def _apply_row_styling(
        self, 
        new_rows, 
        reference_row, 
        default_text_format, 
        default_alignment, 
        default_background_color
    ):
        """
        Apply styling to newly added rows.
        
        Args:
            new_rows: List of newly created row objects
            reference_row: Row to copy style from (if provided)
            default_text_format: Default text formatting
            default_alignment: Default alignment
            default_background_color: Default background color
        """
        
        for new_row in new_rows:
            # Apply styling to each cell in the new row
            for col_idx, new_cell in enumerate(new_row.cells):
                # Determine reference cell for style copying
                reference_cell = None
                if reference_row and col_idx < len(reference_row.cells):
                    reference_cell = reference_row.cells[col_idx]
                
                # Copy style from reference cell if available
                if reference_cell:
                    self._copy_cell_style(new_cell, reference_cell)
                
                # Apply default formatting if no reference or to override
                if default_text_format or default_alignment or default_background_color:
                    self._apply_default_cell_formatting(
                        new_cell, 
                        default_text_format, 
                        default_alignment, 
                        default_background_color
                    )
    
    def _copy_cell_style(self, target_cell, source_cell):
        """
        Copy all styling from source cell to target cell.
        
        Args:
            target_cell: Cell to apply styling to
            source_cell: Cell to copy styling from
        """
        try:
            # Copy paragraph formatting
            for target_para, source_para in zip(target_cell.paragraphs, source_cell.paragraphs):
                # Copy paragraph alignment
                target_para.alignment = source_para.alignment
                
                # Copy run formatting
                if source_para.runs:
                    # Clear existing runs in target
                    for run in target_para.runs:
                        run._element.getparent().remove(run._element)
                    
                    # Copy runs from source
                    for source_run in source_para.runs:
                        new_run = target_para.add_run("")
                        
                        # Copy font properties
                        if source_run.font.name:
                            new_run.font.name = source_run.font.name
                        if source_run.font.size:
                            new_run.font.size = source_run.font.size
                        if source_run.font.color.rgb:
                            new_run.font.color.rgb = source_run.font.color.rgb
                        
                        new_run.bold = source_run.bold
                        new_run.italic = source_run.italic
                        new_run.underline = source_run.underline
            
            # Copy cell background color
            try:
                source_shading = source_cell._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}shd')
                if source_shading is not None:
                    target_shading = target_cell._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}shd')
                    if target_shading is None:
                        # Create shading element
                        target_shading = OxmlElement('w:shd')
                        target_cell._element.get_or_add_tcPr().append(target_shading)
                    
                    # Copy fill attribute
                    if source_shading.get(qn('w:fill')):
                        target_shading.set(qn('w:fill'), source_shading.get(qn('w:fill')))
            except Exception:
                pass  # Ignore background color copy errors
            
            # Copy cell vertical alignment
            try:
                source_valign = source_cell._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}vAlign')
                if source_valign is not None:
                    target_valign = target_cell._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}vAlign')
                    if target_valign is None:
                        target_valign = OxmlElement('w:vAlign')
                        target_cell._element.get_or_add_tcPr().append(target_valign)
                    target_valign.set(qn('w:val'), source_valign.get(qn('w:val')))
            except Exception:
                pass  # Ignore vertical alignment copy errors
            
            # Copy cell borders
            try:
                ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                source_borders = source_cell._element.find(f'.//{{{ns}}}tcBorders')
                if source_borders is not None:
                    # Get or create target tcPr
                    target_tcPr = target_cell._element.get_or_add_tcPr()
                    
                    # Remove existing borders
                    existing_borders = target_tcPr.find(f'.//{{{ns}}}tcBorders')
                    if existing_borders is not None:
                        target_tcPr.remove(existing_borders)
                    
                    # Clone the entire tcBorders element
                    import copy
                    new_borders = copy.deepcopy(source_borders)
                    target_tcPr.append(new_borders)
            except Exception:
                pass  # Ignore border copy errors
                
        except Exception as e:
            # If copying fails, just continue - better to have unstyled cells than no cells
            pass
    
    def _apply_default_cell_formatting(
        self, 
        cell, 
        text_format, 
        alignment, 
        background_color
    ):
        """
        Apply default formatting to a cell.
        
        Args:
            cell: Cell to format
            text_format: TextFormat object with formatting
            alignment: CellAlignment object with alignment
            background_color: Background color as hex string
        """
        try:
            # Apply text formatting
            if text_format:
                for paragraph in cell.paragraphs:
                    if not paragraph.runs:
                        paragraph.add_run("")
                    
                    for run in paragraph.runs:
                        if text_format.font_family:
                            run.font.name = text_format.font_family
                        if text_format.font_size:
                            run.font.size = Pt(text_format.font_size)
                        if text_format.font_color:
                            # Parse hex color
                            try:
                                color_hex = text_format.font_color.lstrip('#')
                                if len(color_hex) == 6:
                                    r = int(color_hex[0:2], 16)
                                    g = int(color_hex[2:4], 16)
                                    b = int(color_hex[4:6], 16)
                                    run.font.color.rgb = RGBColor(r, g, b)
                            except Exception:
                                pass
                        
                        if text_format.bold is not None:
                            run.bold = text_format.bold
                        if text_format.italic is not None:
                            run.italic = text_format.italic
                        if text_format.underline is not None:
                            run.underline = text_format.underline
            
            # Apply alignment
            if alignment:
                for paragraph in cell.paragraphs:
                    if alignment.horizontal:
                        alignment_map = {
                            'left': WD_ALIGN_PARAGRAPH.LEFT,
                            'center': WD_ALIGN_PARAGRAPH.CENTER,
                            'right': WD_ALIGN_PARAGRAPH.RIGHT,
                            'justify': WD_ALIGN_PARAGRAPH.JUSTIFY
                        }
                        if alignment.horizontal.lower() in alignment_map:
                            paragraph.alignment = alignment_map[alignment.horizontal.lower()]
                
                # Apply vertical alignment
                if alignment.vertical:
                    try:
                        v_align = alignment.vertical.lower()
                        if v_align == 'middle':
                            v_align = 'center'
                        
                        valign_element = cell._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}vAlign')
                        if valign_element is None:
                            valign_element = OxmlElement('w:vAlign')
                            cell._element.get_or_add_tcPr().append(valign_element)
                        valign_element.set(qn('w:val'), v_align)
                    except Exception:
                        pass
            
            # Apply background color
            if background_color:
                try:
                    color_hex = background_color.lstrip('#').upper()
                    if len(color_hex) == 6:
                        shading = cell._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}shd')
                        if shading is None:
                            shading = OxmlElement('w:shd')
                            cell._element.get_or_add_tcPr().append(shading)
                        shading.set(qn('w:fill'), color_hex)
                except Exception:
                    pass
                    
        except Exception as e:
            # If formatting fails, continue - better to have unformatted cells than no cells
            pass
    
    def _extract_border_data(self, borders_dict: dict) -> dict:
        """
        Extract border data from extract_cell_formatting format to expected format.
        
        Args:
            borders_dict: Border data from extract_cell_formatting
            
        Returns:
            Border data in expected format for get_cell_value
        """
        result = {}
        
        for side in ['top', 'bottom', 'left', 'right']:
            border_info = borders_dict.get(side, {})
            if border_info:
                result[f'{side}_style'] = border_info.get('style')
                result[f'{side}_width'] = border_info.get('width')
                result[f'{side}_color'] = border_info.get('color')
            else:
                result[f'{side}_style'] = None
                result[f'{side}_width'] = None
                result[f'{side}_color'] = None
        
        return result
    
    def merge_cells(
        self,
        file_path: str,
        table_index: int,
        start_row: int,
        start_col: int,
        end_row: int,
        end_col: int
    ) -> OperationResponse:
        """
        Merge cells in a table to create a merged cell region.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            start_row: Starting row index (top-left corner)
            start_col: Starting column index (top-left corner)
            end_row: Ending row index (bottom-right corner)
            end_col: Ending column index (bottom-right corner)
            
        Returns:
            OperationResponse with merge result
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            # Validate cell positions
            validate_cell_position(start_row, start_col, len(table.rows), len(table.columns))
            validate_cell_position(end_row, end_col, len(table.rows), len(table.columns))
            
            # Validate merge range
            if start_row > end_row or start_col > end_col:
                return OperationResponse.error("Invalid merge range: start position must be before end position")
            
            if start_row == end_row and start_col == end_col:
                return OperationResponse.error("Cannot merge a single cell with itself")
            
            # Check if any cells in the range are already merged
            for row_idx in range(start_row, end_row + 1):
                for col_idx in range(start_col, end_col + 1):
                    cell = table.cell(row_idx, col_idx)
                    merge_info = analyze_cell_merge(cell, row_idx, col_idx)
                    if merge_info and merge_info.merge_type != CellMergeType.NONE:
                        return OperationResponse.error(
                            f"Cell at row {row_idx}, col {col_idx} is already merged. "
                            "Cannot merge cells that are part of existing merged regions."
                        )
            
            # Get the top-left cell (this will be the merged cell)
            merged_cell = table.cell(start_row, start_col)
            
            # Store content from all cells in the range
            all_content = []
            for row_idx in range(start_row, end_row + 1):
                for col_idx in range(start_col, end_col + 1):
                    cell = table.cell(row_idx, col_idx)
                    if cell.text.strip():
                        all_content.append(cell.text.strip())
            
            # Combine all content (separated by spaces)
            combined_content = " ".join(all_content)
            
            # Perform the merge using python-docx's merge method
            merged_cell = table.cell(start_row, start_col)
            merged_cell.merge(table.cell(end_row, end_col))
            
            # Set the combined content
            merged_cell.text = combined_content
            
            # Calculate merge dimensions
            span_rows = end_row - start_row + 1
            span_cols = end_col - start_col + 1
            
            data = {
                "table_index": table_index,
                "start_row": start_row,
                "start_col": start_col,
                "end_row": end_row,
                "end_col": end_col,
                "span_rows": span_rows,
                "span_cols": span_cols,
                "merged_content": combined_content,
                "cells_merged": span_rows * span_cols
            }
            
            return OperationResponse.success(
                f"Successfully merged {span_rows}x{span_cols} cells starting at row {start_row}, col {start_col}",
                data
            )
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to merge cells: {str(e)}")
    
    def unmerge_cells(
        self,
        file_path: str,
        table_index: int,
        row: int,
        column: int
    ) -> OperationResponse:
        """
        Unmerge a merged cell region, splitting it back into individual cells.
        
        Args:
            file_path: Path to the document
            table_index: Index of the table
            row: Row index of any cell in the merged region
            column: Column index of any cell in the merged region
            
        Returns:
            OperationResponse with unmerge result
        """
        try:
            document = self.document_manager.get_or_load_document(file_path)
            
            validate_table_index(table_index, len(document.tables))
            table = document.tables[table_index]
            
            validate_cell_position(row, column, len(table.rows), len(table.columns))
            
            # Get the cell and check if it's part of a merged region
            cell = table.cell(row, column)
            merge_info = analyze_cell_merge(cell, row, column)
            
            if not merge_info or merge_info.merge_type == CellMergeType.NONE:
                return OperationResponse.error(
                    f"Cell at row {row}, col {column} is not part of a merged region"
                )
            
            # Store the content before unmerging
            original_content = cell.text
            
            # Get the actual merged cell (top-left corner)
            merged_cell = table.cell(merge_info.start_row, merge_info.start_col)
            
            # Store merge dimensions
            span_rows = merge_info.span_rows
            span_cols = merge_info.span_cols
            
            # Unmerge by splitting the merged cell
            # This is done by removing the gridSpan and vMerge attributes
            try:
                # Get the table element
                table_element = table._element
                
                # Find the merged cell element
                merged_cell_element = merged_cell._element
                
                # Remove gridSpan and vMerge attributes from all cells in the merged region
                for row_idx in range(merge_info.start_row, merge_info.end_row + 1):
                    for col_idx in range(merge_info.start_col, merge_info.end_col + 1):
                        current_cell = table.cell(row_idx, col_idx)
                        current_cell_element = current_cell._element
                        
                        # Get tcPr element
                        tc_pr = current_cell_element.find(qn('w:tcPr'))
                        if tc_pr is not None:
                            # Remove gridSpan
                            grid_span = tc_pr.find(qn('w:gridSpan'))
                            if grid_span is not None:
                                tc_pr.remove(grid_span)
                            
                            # Remove vMerge
                            v_merge = tc_pr.find(qn('w:vMerge'))
                            if v_merge is not None:
                                tc_pr.remove(v_merge)
                            
                            # If this is not the top-left cell, remove the cell content
                            if row_idx != merge_info.start_row or col_idx != merge_info.start_col:
                                # Clear the cell content
                                current_cell.text = ""
                
                # Distribute content to individual cells (optional)
                # For now, we'll keep the content in the top-left cell only
                # Users can manually distribute content if needed
                
            except Exception as e:
                return OperationResponse.error(f"Failed to unmerge cells: {str(e)}")
            
            data = {
                "table_index": table_index,
                "original_merged_region": {
                    "start_row": merge_info.start_row,
                    "start_col": merge_info.start_col,
                    "end_row": merge_info.end_row,
                    "end_col": merge_info.end_col,
                    "span_rows": span_rows,
                    "span_cols": span_cols
                },
                "unmerged_at": {
                    "row": row,
                    "column": column
                },
                "original_content": original_content,
                "cells_unmerged": span_rows * span_cols
            }
            
            return OperationResponse.success(
                f"Successfully unmerged {span_rows}x{span_cols} cell region starting at row {merge_info.start_row}, col {merge_info.start_col}",
                data
            )
            
        except (InvalidTableIndexError, InvalidCellPositionError) as e:
            return OperationResponse.error(str(e))
        except Exception as e:
            return OperationResponse.error(f"Failed to unmerge cells: {str(e)}")

    def _apply_column_styling_after_add(
        self, 
        table, 
        original_cols, 
        count, 
        column_index,
        reference_column, 
        default_text_format, 
        default_alignment, 
        default_background_color
    ):
        """
        Apply styling to newly added column cells after they have been added.
        
        Args:
            table: The table object
            original_cols: Original number of columns
            count: Number of columns added
            position: Position where columns were added
            column_index: Column index for at_index position
            reference_column: List of cells to copy style from (if provided)
            default_text_format: Default text formatting
            default_alignment: Default alignment
            default_background_color: Default background color
        """
        
        # Determine which columns were added based on the new index semantics
        if column_index is None:
            # Appended at end
            new_column_indices = list(range(original_cols, original_cols + count))
        elif column_index == -1:
            # Inserted before first column -> new columns occupy indices 0..count-1
            new_column_indices = list(range(0, count))
        else:
            # Inserted after column_index -> new columns start at column_index + 1
            new_column_indices = list(range(column_index + 1, column_index + 1 + count))
        
        # Apply styling to each new column
        for col_idx in new_column_indices:
            for row_idx, row in enumerate(table.rows):
                if col_idx < len(row.cells):
                    new_cell = row.cells[col_idx]
                    
                    # Determine reference cell for style copying
                    reference_cell = None
                    if reference_column and row_idx < len(reference_column):
                        reference_cell = reference_column[row_idx]
                    
                    # Copy style from reference cell if available
                    if reference_cell:
                        self._copy_cell_style(new_cell, reference_cell)
                    
                    # Apply default formatting if no reference or to override
                    if default_text_format or default_alignment or default_background_color:
                        self._apply_default_cell_formatting(
                            new_cell, 
                            default_text_format, 
                            default_alignment, 
                            default_background_color
                        )
