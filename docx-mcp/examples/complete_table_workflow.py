#!/usr/bin/env python3
"""
DOCX-MCP Complete Table Workflow Example

This example demonstrates all current table operations available in DOCX-MCP,
showcasing the comprehensive table manipulation capabilities.
"""

import sys
import os
from pathlib import Path

# Add the src directory to the path for development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from docx_mcp.core.document_manager import DocumentManager
from docx_mcp.operations.tables.table_operations import TableOperations


def main():
    """Demonstrate complete table workflow with DOCX-MCP."""
    
    print("🚀 DOCX-MCP Complete Table Workflow Demo")
    print("=" * 50)
    
    # Initialize managers
    doc_manager = DocumentManager()
    table_ops = TableOperations(doc_manager)
    
    document_path = "demo_document.docx"
    
    try:
        # Step 1: Create/Open Document
        print("\n📄 Step 1: Creating document...")
        result = doc_manager.open_document(document_path, create_if_not_exists=True)
        if result.status.value == "success":
            print(f"✅ {result.message}")
            print(f"   Document info: {result.data}")
        else:
            print(f"❌ Failed: {result.message}")
            return
        
        # Step 2: Create a Sales Report Table
        print("\n📊 Step 2: Creating sales report table...")
        headers = ["Product", "Q1 Sales", "Q2 Sales", "Q3 Sales", "Q4 Sales", "Total"]
        result = table_ops.create_table(
            document_path, 
            rows=5,  # 4 data rows + 1 header
            cols=6, 
            headers=headers
        )
        if result.status.value == "success":
            print(f"✅ Created table with {len(headers)} columns")
            print(f"   Table info: {result.data}")
        
        # Step 3: Populate table with sample data
        print("\n💾 Step 3: Adding sample sales data...")
        sales_data = [
            ["Widget A", "15000", "18000", "22000", "25000", "80000"],
            ["Widget B", "12000", "14000", "16000", "18000", "60000"],
            ["Widget C", "8000", "9500", "11000", "13500", "42000"],
            ["Widget D", "5000", "6000", "7500", "9000", "27500"]
        ]
        
        for row_idx, row_data in enumerate(sales_data):
            for col_idx, value in enumerate(row_data):
                result = table_ops.set_cell_value(
                    document_path, 0, row_idx + 1, col_idx, value
                )
                if result.status.value != "success":
                    print(f"⚠️ Warning: Failed to set cell [{row_idx+1}, {col_idx}]: {result.message}")
        
        print("✅ Sample data added successfully")
        
        # Step 4: Demonstrate data retrieval
        print("\n📋 Step 4: Retrieving table data...")
        result = table_ops.get_table_data_and_structure(document_path, 0, include_headers=True, format_type="array")
        if result.status.value == "success":
            print("✅ Table data retrieved (array format):")
            for i, row in enumerate(result.data["data"]):
                row_type = "Header" if i == 0 else f"Row {i}"
                print(f"   {row_type}: {row}")
        
        # Step 5: Get specific cell values
        print("\n🔍 Step 5: Reading specific cell values...")
        # Get Q4 sales for Widget A
        result = table_ops.get_cell_value(document_path, 0, 1, 4)  # Row 1, Col 4 (Q4 Sales)
        if result.status.value == "success":
            print(f"✅ Widget A Q4 Sales: {result.data['value']}")
        
        # Step 6: Add more data (new row)
        print("\n➕ Step 6: Adding new product row...")
        result = table_ops.add_table_rows(document_path, 0, count=1, position="end")
        if result.status.value == "success":
            print("✅ New row added")
            
            # Add data for new product
            new_product_data = ["Widget E", "3000", "4000", "5500", "7000", "19500"]
            for col_idx, value in enumerate(new_product_data):
                table_ops.set_cell_value(document_path, 0, 5, col_idx, value)  # Row 5 (new row)
            print("✅ New product data added")
        
        # Step 7: Add summary column
        print("\n📈 Step 7: Adding summary column...")
        result = table_ops.add_table_columns(document_path, 0, count=1, position="end")
        if result.status.value == "success":
            print("✅ New column added")
            
            # Add column header
            table_ops.set_cell_value(document_path, 0, 0, 6, "Average")
            
            # Add average calculations (as text for now)
            averages = ["20000", "15000", "10500", "6875", "4875"]
            for row_idx, avg_value in enumerate(averages):
                table_ops.set_cell_value(document_path, 0, row_idx + 1, 6, avg_value)
            print("✅ Average column populated")
        
        # Step 8: Create a second table (summary table)
        print("\n📊 Step 8: Creating summary table...")
        result = table_ops.create_table(
            document_path,
            rows=3,
            cols=2,
            headers=["Metric", "Value"]
        )
        if result.status.value == "success":
            print("✅ Summary table created")
            
            # Add summary data
            summary_data = [
                ["Total Products", "5"],
                ["Best Performer", "Widget A"],
                ["Total Revenue", "$229,000"]
            ]
            
            for row_idx, (metric, value) in enumerate(summary_data):
                table_ops.set_cell_value(document_path, 1, row_idx + 1, 0, metric)
                table_ops.set_cell_value(document_path, 1, row_idx + 1, 1, value)
            print("✅ Summary data added")
        
        # Step 9: List all tables
        print("\n📋 Step 9: Listing all tables in document...")
        result = table_ops.list_tables(document_path, include_summary=True)
        if result.status.value == "success":
            print(f"✅ Found {len(result.data['tables'])} tables:")
            for i, table_info in enumerate(result.data['tables']):
                total_cells = table_info['rows'] * table_info['columns']
                print(f"   Table {i}: {table_info['rows']}x{table_info['columns']} "
                      f"({total_cells} cells)")
        
        # Step 10: Get table data in different formats
        print("\n🔄 Step 10: Exporting table data in different formats...")
        
        # Array format (default)
        result = table_ops.get_table_data_and_structure(document_path, 0, format_type="array")
        print(f"✅ Array format: {len(result.data['data'])} rows")
        
        # Object format
        result = table_ops.get_table_data_and_structure(document_path, 0, format_type="object")
        if result.status.value == "success":
            print(f"✅ Object format: {len(result.data['data'])} records")
            print(f"   Sample record: {result.data['data'][0] if result.data['data'] else 'None'}")
        
        # CSV format
        result = table_ops.get_table_data_and_structure(document_path, 0, format_type="csv")
        if result.status.value == "success":
            print("✅ CSV format generated")
            print("   First few lines:")
            csv_data = result.data['data']
            if isinstance(csv_data, str):
                csv_lines = csv_data.split('\n')[:3]
                for line in csv_lines:
                    print(f"   {line}")
            else:
                # If it's a list, show first few items
                for i, line in enumerate(csv_data[:3]):
                    print(f"   {line}")
        
        # Step 11: Save the document
        print("\n💾 Step 11: Saving document...")
        result = doc_manager.save_document(document_path)
        if result.status.value == "success":
            print(f"✅ Document saved: {result.message}")
        
        # Step 12: Document information
        print("\n📊 Step 12: Final document information...")
        result = doc_manager.get_document_info(document_path)
        if result.status.value == "success":
            info = result.data
            print("✅ Final document stats:")
            print(f"   📄 File: {info['file_path']}")
            print(f"   📊 Tables: {info['table_count']}")
            print(f"   📝 Paragraphs: {info['paragraph_count']}")
            print(f"   📏 File size: {info.get('file_size', 'Unknown')}")
        
        print("\n🎉 Complete table workflow demonstration finished!")
        print(f"📁 Check the generated file: {Path(document_path).absolute()}")
        
    except Exception as e:
        print(f"\n❌ Error during workflow: {str(e)}")
        import traceback
        traceback.print_exc()
    
    finally:
        print("\n" + "=" * 50)
        print("💡 This demo showcases DOCX-MCP's current table capabilities.")
        print("🔮 Future versions will add formatting, import/export, and search features!")


if __name__ == "__main__":
    main()
