"""
EXCEL BUILDER: creates formatted Excel workbooks for academic data.

Usage (programmatic):
    from fieldkit.academic.build_xlsx import AcademicWorkbook
    wb = AcademicWorkbook("Analysis Results")
    wb.add_data_sheet("Results", headers, data_rows)
    wb.save("output.xlsx")
"""
import os

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter

class AcademicWorkbook:
    def __init__(self, title="Academic Data"):
        self.wb = Workbook()
        self.title = title
        # Remove default sheet
        self.wb.remove(self.wb.active)

    def add_data_sheet(self, sheet_name, headers, rows):
        ws = self.wb.create_sheet(sheet_name)
        # openpyxl leaves the paper size unset, which Excel prints as US
        # Letter. The country decides (A4 everywhere but the US).
        from .layout import current
        lay = current()
        ws.page_setup.paperSize = ws.PAPERSIZE_LETTER if lay["page"] == "Letter" else ws.PAPERSIZE_A4
        font = lay["font"]

        # Header formatting
        header_font = Font(name=font, size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        # Write headers
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Write data
        data_font = Font(name=font, size=11)
        data_align = Alignment(vertical="top", wrap_text=True)

        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = data_font
                cell.alignment = data_align
                cell.border = thin_border
                # Alternate row colours
                if row_idx % 2 == 0:
                    cell.fill = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")

        # Auto-width columns
        for col in range(1, len(headers) + 1):
            max_len = max(
                len(str(ws.cell(row=r, column=col).value or ""))
                for r in range(1, len(rows) + 2)
            )
            ws.column_dimensions[get_column_letter(col)].width = min(max_len + 4, 50)

        return ws

    def save(self, output_path, verbose=True):
        from .workspace import refuse_cloud_save
        refuse_cloud_save(output_path, "the Excel workbook")
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        self.wb.save(output_path)
        if verbose:
            print("Excel workbook saved: %s" % output_path)
