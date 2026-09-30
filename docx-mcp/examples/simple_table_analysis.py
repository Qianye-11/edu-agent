#!/usr/bin/env python3
"""
Simple example showing how to use the table analysis API.
"""

import os
import sys
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from docx_mcp.core.document_manager import DocumentManager
from docx_mcp.operations.tables.table_operations import TableOperations
import json


def main():
    """Simple demonstration of table analysis."""
    print("Simple Table Analysis Example")
    print("=" * 50)
    
    # Setup
    doc_manager = DocumentManager()
    table_ops = TableOperations(doc_manager)
    
    # Create a simple test document
    from docx import Document
    
    doc = Document()
    doc.add_heading('Test Document', 0)
    
    # Create a simple table
    table = doc.add_table(rows=3, cols=3)
    table.style = 'Table Grid'
    
    # Add some data
    data = [
        ['Name', 'Age', 'City'],
        ['Alice', '25', 'New York'],
        ['Bob', '30', 'London']
    ]
    
    for i, row_data in enumerate(data):
        row = table.rows[i]
        for j, cell_data in enumerate(row_data):
            row.cells[j].text = cell_data
            # Make header bold
            if i == 0:
                for paragraph in row.cells[j].paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True
    
    # Save test document
    test_file = "simple_test.docx"
    doc.save(test_file)
    
    try:
        # Open document using the API
        result = doc_manager.open_document(test_file)
        print(f"Document opened: {result.success}")
        
        if result.success:
            # Analyze single table via get_table_data_and_structure
            print("\n--- Single Table Analysis ---")
            table_result = table_ops.get_table_data_and_structure(test_file, 0, include_headers=True, format_type="array")
            
            if table_result.success:
                data = table_result.data
                print(f"Table has {data['rows']} data rows and {data['columns']} columns")
                print(f"Headers detected: {data['has_headers']}")
                if data['headers']:
                    print(f"Header cells: {data['headers']}")
                
                print(f"Merged regions: {len(data['merge_regions'])}")
                
                # Show table data
                print("\nTable data:")
                for i, row in enumerate(data['data']):
                    print(f"  Row {i}: {row}")
                
                # Get styles separately
                styles_result = table_ops.get_table_styles(test_file, 0)
                if styles_result.success:
                    styles = styles_result.data
                    print(f"\nStyle summary:")
                    print(f"  Font families: {styles['style_summary']['font_families']}")
                    print(f"  Font sizes: {styles['style_summary']['font_sizes']}")
                    print(f"  Colors: {styles['style_summary']['colors']}")
            
            # Note: analyze_all_tables removed; end of demo
    
    finally:
        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)
            print(f"\nCleaned up: {test_file}")
    
    print("\nThis API provides comprehensive table structure analysis")
    print("including cell merging, formatting, alignment, and style information.")
    print("Perfect for AI models that need to understand and preserve table formatting!")


if __name__ == "__main__":
    main()
