# DOCX-MCP: Word Document MCP Server

A Model Context Protocol (MCP) server for Microsoft Word document operations, built with FastMCP and python-docx. Focused on advanced table operations with comprehensive formatting and analysis capabilities.

## 🚀 Core Features

### Document Operations
- `open_document` - Open/create Word documents
- `save_document` - Save documents with optional rename
- `get_document_info` - Get document information and metadata

### Table Structure Operations
- `create_table` - Create tables with customizable dimensions and headers
- `delete_table` - Delete tables by index
- `add_table_rows` - Add/remove rows at any position with styling
- `add_table_columns` - Add/remove columns at any position with styling
- `delete_table_rows` - Delete specific rows from tables
- `merge_cells` - Merge cells in rectangular regions
- `unmerge_cells` - Split merged cell regions back into individual cells

### Table Data Operations
- `set_cells_value` - Batch set multiple cells efficiently with comprehensive formatting options
- `get_table_data_and_structure` - Get table data and structure (array, object, CSV formats)
- `get_table_styles` - Get table cell styles and formatting information
- `list_tables` - List all tables with metadata

### Table Search & Analysis
- `search_table_content` - Search content across all table cells
- `search_table_headers` - Search specifically in table headers

### Cell Formatting Features
- **Text Formatting**: Font family, size, color, bold, italic, underline, strikethrough
- **Alignment**: Horizontal (left, center, right, justify) and vertical (top, middle, bottom)
- **Visual Styling**: Background colors (hex), borders with customizable styles/widths/colors
- **Style Preservation**: Maintain existing formatting when updating cells
- **Batch Operations**: Efficiently set multiple cells with individual formatting in one call

## 📦 Installation

```bash
# Clone and install
git clone <repository-url>
cd docx_table
pip install -r requirements.txt
pip install -e .
```

## 🖥️ Usage

### Run MCP Server

```bash
# Default STDIO transport
python -m docx_mcp.server

# HTTP/SSE transports
python -m docx_mcp.server --transport sse --host localhost --port 8000
python -m docx_mcp.server --transport streamable-http --host localhost --port 8000
```

### Command Line Options

```bash
python -m docx_mcp.server --help
```

**Available options:**
- `--transport {stdio,sse,streamable-http}` - Transport protocol (default: stdio)
- `--host HOST` - Host for HTTP/SSE transports (default: localhost)
- `--port PORT` - Port for HTTP/SSE transports (default: 8000)
- `--no-banner` - Disable startup banner

### Direct Usage

```python
from docx_mcp.core.document_manager import DocumentManager
from docx_mcp.operations.tables.table_operations import TableOperations

# Initialize
doc_manager = DocumentManager()
table_ops = TableOperations(doc_manager)

# Create table with headers (auto-loads document)
result = table_ops.create_table("document.docx", rows=3, cols=4, 
                               headers=["Name", "Age", "City", "Occupation"])

# Set multiple cells with formatting
cells = [
    {"row_index": 1, "column_index": 0, "value": "Alice", "font_family": "Arial", "bold": True},
    {"row_index": 1, "column_index": 1, "value": "25", "font_size": 12}
]
result = table_ops.set_cells_value("document.docx", 0, cells)

# Get table data and structure
result = table_ops.get_table_data_and_structure("document.docx", 0, include_headers=True)

# Merge cells in a 2x2 region (rows 1-2, cols 1-2)
result = table_ops.merge_cells("document.docx", 0, 1, 1, 2, 2)

# Unmerge cells (specify any cell in the merged region)
result = table_ops.unmerge_cells("document.docx", 0, 1, 1)
```

## 🔧 MCP Tools

All tools work independently - no pre-loading required. Each tool accepts JSON parameters and returns JSON responses.

### Interface Design Philosophy

The MCP interface is designed for **efficiency and simplicity**:
- **Batch Operations**: Use `set_cells_value` for setting multiple cells efficiently
- **Data/Style Separation**: Use `get_table_data_and_structure` for content and `get_table_styles` for formatting
- **Range Queries**: Both data and style interfaces support row/column ranges for large tables
- **Context-Aware**: Optimized for AI model usage with minimal API calls

### Document Operations
- `open_document(file_path, create_if_not_exists=True)` - Open/create Word documents
- `save_document(file_path, save_as=None)` - Save documents with optional rename  
- `get_document_info(file_path)` - Get document information and metadata

### Table Operations
- `create_table(file_path, rows, cols, headers=None)` - Create tables with headers
- `delete_table(file_path, table_index)` - Delete tables by index
- `add_table_rows(file_path, table_index, count, position="end")` - Add/remove rows
- `add_table_columns(file_path, table_index, count, position="end")` - Add/remove columns
- `delete_table_rows(file_path, table_index, row_indices)` - Delete specific rows

### Data Operations  
- `set_cells_value(file_path, table_index, cells)` - Batch set multiple cells with formatting
- `get_table_data_and_structure(file_path, table_index, format="array")` - Get table data and structure (array/object/CSV)
- `get_table_styles(file_path, table_index)` - Get table cell styles and formatting
- `list_tables(file_path)` - List all tables with metadata

### Cell Merge Operations
- `merge_cells(file_path, table_index, start_row, start_col, end_row, end_col)` - Merge cells in rectangular regions
- `unmerge_cells(file_path, table_index, row, column)` - Split merged cell regions back into individual cells

### Search & Analysis
- `search_table_content(file_path, query, search_mode="contains")` - Search table content
- `search_table_headers(file_path, query)` - Search table headers

## 🧪 Testing

```bash
pytest                    # Run all tests
pytest --cov=src/docx_mcp # Run with coverage
pytest -v                 # Verbose output
```

## 📄 License

MIT License - see LICENSE file for details.