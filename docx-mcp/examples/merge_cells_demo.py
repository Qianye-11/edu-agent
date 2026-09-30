#!/usr/bin/env python3
"""
Demo script for cell merge and unmerge operations.
"""

import os
import sys
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from docx_mcp.core.document_manager import DocumentManager
from docx_mcp.operations.tables.table_operations import TableOperations

def main():
    """Demonstrate cell merge and unmerge operations."""
    
    # Create a test document
    doc_path = "merge_demo.docx"
    
    print("Creating a test document with a table...")
    
    # Initialize managers
    document_manager = DocumentManager()
    table_operations = TableOperations(document_manager)
    
    # Open/create document
    result = document_manager.open_document(doc_path, create_if_not_exists=True)
    print(f"Document operation: {result}")
    
    # Create a 4x4 table
    result = table_operations.create_table(doc_path, rows=4, cols=4, headers=["Header 1", "Header 2", "Header 3", "Header 4"])
    print(f"Table creation: {result}")
    
    if not result.success:
        print("Failed to create table")
        return
    
    table_index = result.data["table_index"]
    
    # Fill some data in the table
    print("\nFilling table with sample data...")
    sample_data = [
        ["A1", "B1", "C1", "D1"],
        ["A2", "B2", "C2", "D2"],
        ["A3", "B3", "C3", "D3"],
        ["A4", "B4", "C4", "D4"]
    ]
    
    for row_idx, row_data in enumerate(sample_data):
        for col_idx, value in enumerate(row_data):
            result = table_operations.set_cell_value(doc_path, table_index, row_idx, col_idx, value)
            if not result.success:
                print(f"Failed to set cell ({row_idx}, {col_idx}): {result}")
    
    print("Table filled with sample data")
    
    # Merge cells in a 2x2 region (rows 1-2, cols 1-2)
    print("\nMerging cells in region (1,1) to (2,2)...")
    result = table_operations.merge_cells(doc_path, table_index, 1, 1, 2, 2)
    print(f"Merge operation: {result}")
    
    if result.success:
        print(f"Successfully merged {result.data['span_rows']}x{result.data['span_cols']} cells")
        print(f"Merged content: '{result.data['merged_content']}'")
    
    # Merge cells in a 1x3 region (row 0, cols 1-3)
    print("\nMerging cells in region (0,1) to (0,3)...")
    result = table_operations.merge_cells(doc_path, table_index, 0, 1, 0, 3)
    print(f"Merge operation: {result}")
    
    if result.success:
        print(f"Successfully merged {result.data['span_rows']}x{result.data['span_cols']} cells")
        print(f"Merged content: '{result.data['merged_content']}'")
    
    # Save the document
    print("\nSaving document...")
    result = document_manager.save_document(doc_path)
    print(f"Save operation: {result}")
    
    # Now demonstrate unmerging
    print("\nUnmerging the 2x2 region...")
    result = table_operations.unmerge_cells(doc_path, table_index, 1, 1)  # Any cell in the merged region
    print(f"Unmerge operation: {result}")
    
    if result.success:
        print(f"Successfully unmerged {result.data['cells_unmerged']} cells")
        print(f"Original content: '{result.data['original_content']}'")
    
    # Save the document again
    print("\nSaving document after unmerge...")
    result = document_manager.save_document(doc_path)
    print(f"Save operation: {result}")
    
    print(f"\nDemo completed! Check the file: {doc_path}")

if __name__ == "__main__":
    main()
