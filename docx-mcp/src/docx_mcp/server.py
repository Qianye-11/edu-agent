"""MCP Server for Word document operations using FastMCP."""

from typing import Any, Dict, List
from fastmcp import FastMCP

from .core.document_manager import DocumentManager
from .operations.tables.table_operations import TableOperations
from .models.responses import OperationResponse
from .models.formatting import TextFormat, CellAlignment, CellBorders, CellFormatting


# Initialize FastMCP app
mcp = FastMCP("Word Document MCP Server")

# Initialize managers
document_manager = DocumentManager()
table_operations = TableOperations(document_manager)


# Note: FastMCP automatically handles JSON parameter validation and conversion
# No need for explicit Pydantic request models when using direct parameter signatures


# Document operations
@mcp.tool()
def open_document(
    file_path: str,
    create_if_not_exists: bool = True
) -> Dict[str, Any]:
    """Open or create a Word document.

    Args:
        file_path: Path to the document file
        create_if_not_exists: Create document if it doesn't exist
    """
    result = document_manager.open_document(
        file_path,
        create_if_not_exists
    )
    return result.to_dict()


@mcp.tool()
def save_document(
    file_path: str,
    save_as: str = None
) -> Dict[str, Any]:
    """Save a Word document.

    Args:
        file_path: Path to the document file
        save_as: Optional path to save as a different file
    """
    result = document_manager.save_document(
        file_path,
        save_as
    )
    return result.to_dict()


@mcp.tool()
def get_document_info(file_path: str) -> Dict[str, Any]:
    """Get information about a document.

    Args:
        file_path: Path to the document file
    """
    result = document_manager.get_document_info(file_path)
    return result.to_dict()


# Table structure operations
@mcp.tool()
def create_table(
    file_path: str,
    rows: int,
    cols: int,
    position: str = "end",
    paragraph_index: int = None,
    headers: List[str] = None
) -> Dict[str, Any]:
    """Create a new table in the document.

    Args:
        file_path: Path to the document file
        rows: Number of rows (must be >= 1)
        cols: Number of columns (must be >= 1)
        position: Position to insert table ("end", "beginning", "after_paragraph")
        paragraph_index: Paragraph index for after_paragraph position
        headers: Optional header row data
    """
    result = table_operations.create_table(
        file_path,
        rows,
        cols,
        position,
        paragraph_index,
        headers
    )
    return result.to_dict()


@mcp.tool()
def delete_table(
    file_path: str,
    table_index: int
) -> Dict[str, Any]:
    """Delete a table from the document.

    Args:
        file_path: Path to the document file
        table_index: Index of the table to delete (>= 0)
    """
    result = table_operations.delete_table(
        file_path,
        table_index
    )
    return result.to_dict()


@mcp.tool()
def add_table_rows(
    file_path: str,
    table_index: int,
    count: int = 1,
    row_index: int = None,
    copy_style_from_row: int = None,
    font_family: str = None,
    font_size: int = None,
    font_color: str = None,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    horizontal_alignment: str = None,
    vertical_alignment: str = None,
    background_color: str = None
) -> Dict[str, Any]:
    """Add rows to a table with optional styling control.

    Args:
        file_path: Path to the document file
        table_index: Index of the table (>= 0)
        count: Number of rows to add (>= 1)
        row_index: Insert position indicator. None=append to end; -1=before first row; x>=0=after row x
        copy_style_from_row: Row index to copy style from (None = auto-detect)
        font_family: Font family for new cells (e.g., "Arial", "Times New Roman")
        font_size: Font size in points (8-72)
        font_color: Font color as hex string (e.g., "FF0000" for red)
        bold: Bold formatting
        italic: Italic formatting
        underline: Underline formatting
        horizontal_alignment: Horizontal alignment ("left", "center", "right", "justify")
        vertical_alignment: Vertical alignment ("top", "middle", "bottom")
        background_color: Background color as hex string (e.g., "FFFF00" for yellow)
    """
    # Create TextFormat if any text formatting parameters are provided
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

    # Create CellAlignment if any alignment parameters are provided
    alignment = None
    if horizontal_alignment or vertical_alignment:
        alignment = CellAlignment(
            horizontal=horizontal_alignment,
            vertical=vertical_alignment
        )

    result = table_operations.add_table_rows(
        file_path,
        table_index,
        count,
        row_index,
        copy_style_from_row,
        text_format,
        alignment,
        background_color
    )
    return result.to_dict()


@mcp.tool()
def add_table_columns(
    file_path: str,
    table_index: int,
    count: int = 1,
    column_index: int = None,
    copy_style_from_column: int = None,
    font_family: str = None,
    font_size: int = None,
    font_color: str = None,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    horizontal_alignment: str = None,
    vertical_alignment: str = None,
    background_color: str = None
) -> Dict[str, Any]:
    """Add columns to a table with optional styling control.

    Args:
        file_path: Path to the document file
        table_index: Index of the table (>= 0)
        count: Number of columns to add (>= 1)
        column_index: Insert position indicator. None=append to end; -1=before first column; x>=0=after column x
        copy_style_from_column: Column index to copy style from (None = auto-detect)
        font_family: Font family for new cells (e.g., "Arial", "Times New Roman")
        font_size: Font size in points (8-72)
        font_color: Font color as hex string (e.g., "FF0000" for red)
        bold: Bold formatting
        italic: Italic formatting
        underline: Underline formatting
        horizontal_alignment: Horizontal alignment ("left", "center", "right", "justify")
        vertical_alignment: Vertical alignment ("top", "middle", "bottom")
        background_color: Background color as hex string (e.g., "FFFF00" for yellow)
    """
    # Create TextFormat if any text formatting parameters are provided
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

    # Create CellAlignment if any alignment parameters are provided
    alignment = None
    if horizontal_alignment or vertical_alignment:
        alignment = CellAlignment(
            horizontal=horizontal_alignment,
            vertical=vertical_alignment
        )

    result = table_operations.add_table_columns(
        file_path,
        table_index,
        count,
        column_index,
        copy_style_from_column,
        text_format,
        alignment,
        background_color
    )
    return result.to_dict()


@mcp.tool()
def delete_table_rows(
    file_path: str,
    table_index: int,
    row_indices: List[int]
) -> Dict[str, Any]:
    """Delete rows from a table.

    Args:
        file_path: Path to the document file
        table_index: Index of the table (>= 0)
        row_indices: List of row indices to delete
    """
    result = table_operations.delete_table_rows(
        file_path,
        table_index,
        row_indices
    )
    return result.to_dict()


@mcp.tool()
def set_cells_value(
    file_path: str,
    table_index: int,
    cells: List[Dict[str, Any]],
    preserve_existing_format: bool = True
) -> Dict[str, Any]:
    """Set values and optional formatting for multiple cells in a table.

    This is a batch operation that allows setting multiple cells at once,
    which is more efficient than calling `set_cell_value` repeatedly.

    Args:
        file_path: Path to the document file (e.g., "D:\\docs\\demo.docx").
        table_index: Index of the target table (>= 0) (e.g., 0).
        cells: List of cell dictionaries. Each item may include:
            - row_index (int): Row index (>= 0) (e.g., 0).
            - column_index (int): Column index (>= 0) (e.g., 2).
            - value (str): Text value to set (e.g., "Total").
            - font_family (Optional[str]): Font family (e.g., "Calibri", "Arial").
            - font_size (Optional[int]): Font size in points (8-72) (e.g., 11).
            - font_color (Optional[str]): Font color hex without '#' (e.g., "333333", "FF0000").
            - bold (Optional[bool]): Bold text (e.g., True).
            - italic (Optional[bool]): Italic text (e.g., False).
            - underline (Optional[bool]): Underline text (e.g., True).
            - horizontal_alignment (Optional[str]): Horizontal alignment; one of
              "left", "center", "right", "justify" (e.g., "center").
            - vertical_alignment (Optional[str]): Vertical alignment; one of
              "top", "middle", "bottom" (e.g., "middle").
            - background_color (Optional[str]): Background color hex without '#'
              (e.g., "F0F0F0", "FFFF00").

        preserve_existing_format: Whether to preserve existing formatting for
            fields not provided (default: True) (e.g., True).

    Returns:
        Dict[str, Any]: Result dictionary (conforms to the unified response structure).

    Example (cells structure):
        cells = [
            {
                "row_index": 0,
                "column_index": 1,
                "value": "Hello",
                "font_family": "Arial",
                "font_size": 12,
                "font_color": "FF0000",
                "bold": True,
                "italic": False,
                "underline": False,
                "horizontal_alignment": "center",
                "vertical_alignment": "middle",
                "background_color": "FFFF00",
            },
            {
                "row_index": 1,
                "column_index": 0,
                "value": "World"
            }
        ]

        # Usage:
        # set_multiple_cells(
        #     file_path="D:\\docs\\demo.docx",
        #     table_index=0,
        #     cells=cells,
        #     preserve_existing_format=True,
        # )
    """
    result = table_operations.set_multiple_cells(
        file_path,
        table_index,
        cells,
        preserve_existing_format
    )
    return result.to_dict()


@mcp.tool()
def get_table_data_and_structure(
    file_path: str,
    table_index: int,
    start_row: int = 0,
    end_row: int = None,
    start_col: int = 0,
    end_col: int = None,
    include_headers: bool = True,
    format: str = "array"
) -> Dict[str, Any]:
    """Get table data and structure information within specified range.

    This interface returns table content, merge information, and basic structure
    without detailed cell formatting to keep response size manageable.

    Args:
        file_path: Path to the document file (e.g., "D:\\docs\\demo.docx").
        table_index: Index of the table (>= 0) (e.g., 0).
        start_row: Starting row index (0-based, inclusive) (e.g., 0).
        end_row: Ending row index (0-based, exclusive). None means to the end (e.g., 10).
        start_col: Starting column index (0-based, inclusive) (e.g., 0).
        end_col: Ending column index (0-based, exclusive). None means to the end (e.g., 5).
        include_headers: Whether to include the first row as headers (e.g., True).
        format: Output format for data; one of "array", "object", "csv" (e.g., "array").

    Returns:
        Dict[str, Any]: Result dictionary containing:
            - table_index (int)
            - format (str)
            - rows (int)
            - columns (int)
            - has_headers (bool)
            - headers (Optional[List[str]])
            - data (Union[List[List[str]], List[Dict[str, str]], List[List[str]]])
            - merge_regions (List[Dict])  # merge information
            - range_info (Dict)  # pagination and range information

    Example:
        # Get first 10 rows and 5 columns of table data with structure
        # get_table_data_and_structure(
        #     file_path="D:\\docs\\demo.docx",
        #     table_index=0,
        #     start_row=0,
        #     end_row=10,
        #     start_col=0,
        #     end_col=5,
        #     include_headers=True,
        #     format="array"
        # )
    """
    result = table_operations.get_table_data_and_structure(
        file_path,
        table_index,
        start_row,
        end_row,
        start_col,
        end_col,
        include_headers,
        format
    )
    return result.to_dict()


@mcp.tool()
def get_table_styles(
    file_path: str,
    table_index: int,
    start_row: int = 0,
    end_row: int = None,
    start_col: int = 0,
    end_col: int = None
) -> Dict[str, Any]:
    """Get table cell styles and formatting information within specified range.

    This interface returns detailed cell formatting information including
    fonts, colors, alignment, borders, and background colors.

    Args:
        file_path: Path to the document file (e.g., "D:\\docs\\demo.docx").
        table_index: Index of the table (>= 0) (e.g., 0).
        start_row: Starting row index (0-based, inclusive) (e.g., 0).
        end_row: Ending row index (0-based, exclusive). None means to the end (e.g., 10).
        start_col: Starting column index (0-based, inclusive) (e.g., 0).
        end_col: Ending column index (0-based, exclusive). None means to the end (e.g., 5).

    Returns:
        Dict[str, Any]: Result dictionary containing:
            - table_index (int)
            - cell_styles (List[List[Dict]])  # detailed cell formatting
            - style_summary (Dict)  # summary of styles used
            - range_info (Dict)  # pagination and range information

    Example:
        # Get styles for first 10 rows and 5 columns
        # get_table_styles(
        #     file_path="D:\\docs\\demo.docx",
        #     table_index=0,
        #     start_row=0,
        #     end_row=10,
        #     start_col=0,
        #     end_col=5
        # )
    """
    result = table_operations.get_table_styles(
        file_path,
        table_index,
        start_row,
        end_row,
        start_col,
        end_col
    )
    return result.to_dict()


# Query operations
@mcp.tool()
def list_tables(
    file_path: str,
    include_summary: bool = True
) -> Dict[str, Any]:
    """List all tables in the document.

    Args:
        file_path: Path to the document file
        include_summary: Whether to include table summary information
    """
    result = table_operations.list_tables(
        file_path,
        include_summary
    )
    return result.to_dict()


# Table search operations
@mcp.tool()
def search_table_content(
    file_path: str,
    query: str,
    search_mode: str = "contains",
    case_sensitive: bool = False,
    table_indices: List[int] = None,
    max_results: int = None
) -> Dict[str, Any]:
    """Search for content within table cells across all or specified tables.

    Args:
        file_path: Path to the document file
        query: Search query string
        search_mode: Search mode ("exact", "contains", "regex")
        case_sensitive: Whether search is case sensitive
        table_indices: Optional list of table indices to search (None = all tables)
        max_results: Maximum number of results to return (None = no limit)
    """
    result = table_operations.search_table_content(
        file_path,
        query,
        search_mode,
        case_sensitive,
        table_indices,
        max_results
    )
    return result.to_dict()


@mcp.tool()
def search_table_headers(
    file_path: str,
    query: str,
    search_mode: str = "contains",
    case_sensitive: bool = False
) -> Dict[str, Any]:
    """Search specifically in table headers (first row of each table).

    Args:
        file_path: Path to the document file
        query: Search query string
        search_mode: Search mode ("exact", "contains", "regex")
        case_sensitive: Whether search is case sensitive
    """
    result = table_operations.search_table_headers(
        file_path,
        query,
        search_mode,
        case_sensitive
    )
    return result.to_dict()


# Note: Individual formatting interfaces have been removed.
# Use set_cell_value or set_multiple_cells with formatting parameters instead.
# These provide the same functionality with better consistency and efficiency.


# Cell merge operations
@mcp.tool()
def merge_cells(
    file_path: str,
    table_index: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int
) -> Dict[str, Any]:
    """Merge cells in a table to create a merged cell region.

    This function merges a rectangular range of cells in a table, combining
    them into a single merged cell. The merged cell will span the specified
    range and contain the combined content from all cells in the range.

    Args:
        file_path: Path to the document file
        table_index: Index of the table (>= 0)
        start_row: Starting row index (top-left corner) (>= 0)
        start_col: Starting column index (top-left corner) (>= 0)
        end_row: Ending row index (bottom-right corner) (>= 0)
        end_col: Ending column index (bottom-right corner) (>= 0)
    """
    result = table_operations.merge_cells(
        file_path,
        table_index,
        start_row,
        start_col,
        end_row,
        end_col
    )
    return result.to_dict()


@mcp.tool()
def unmerge_cells(
    file_path: str,
    table_index: int,
    row: int,
    column: int
) -> Dict[str, Any]:
    """Unmerge a merged cell region, splitting it back into individual cells.

    This function splits a merged cell region back into individual cells.
    You can specify any cell within the merged region, and it will unmerge
    the entire region. The content will remain in the top-left cell of the
    original merged region.

    Args:
        file_path: Path to the document file
        table_index: Index of the table (>= 0)
        row: Row index of any cell in the merged region (>= 0)
        column: Column index of any cell in the merged region (>= 0)
    """
    result = table_operations.unmerge_cells(
        file_path,
        table_index,
        row,
        column
    )
    return result.to_dict()


# Table analysis operations have been merged into get_table_data via the include_cell_details flag.


def main():
    """Main entry point to run the MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="Word Document MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol to use (default: stdio)"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind to for HTTP/SSE transports (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to for HTTP/SSE transports (default: 8000)"
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Disable startup banner"
    )

    args = parser.parse_args()

    # Prepare transport kwargs for HTTP/SSE
    transport_kwargs = {}
    if args.transport in ["sse", "streamable-http"]:
        transport_kwargs["host"] = args.host
        transport_kwargs["port"] = args.port

    print(f"Starting Word Document MCP Server with {args.transport} transport...")
    if args.transport in ["sse", "streamable-http"]:
        print(f"Server will be available at http://{args.host}:{args.port}")

    mcp.run(
        transport=args.transport,
        show_banner=not args.no_banner,
        **transport_kwargs
    )


if __name__ == "__main__":
    main()
