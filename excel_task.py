import re
from pathlib import Path


CELL_ASSIGNMENT = re.compile(r"^([A-Za-z]{1,3}[1-9][0-9]{0,6})\s*=\s*(.+)$")
EXCEL_FILE_EXTENSIONS = {".xlsx", ".xlsm"}


def parse_cell_assignments(raw_text):
	assignments = []
	seen_cells = set()
	for line_number, line in enumerate(raw_text.splitlines(), start=1):
		line = line.strip()
		if not line:
			continue
		match = CELL_ASSIGNMENT.fullmatch(line)
		if match is None:
			match = re.fullmatch(
				r"([A-Za-z]{1,3}[1-9][0-9]{0,6})\s+(?:equals|is)\s+(.+)",
				line,
				re.IGNORECASE,
			)
		if match is None:
			raise ValueError(
				f"Line {line_number} must use CELL=value, for example A1=Quarterly report."
			)
		cell, value = match.groups()
		cell = cell.upper()
		column_number = 0
		address_match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", cell)
		column_letters, row_number = address_match.groups()
		for character in column_letters:
			column_number = column_number * 26 + ord(character) - ord("A") + 1
		if column_number > 16384 or int(row_number) > 1048576:
			raise ValueError(f"Cell {cell} is outside Excel's worksheet limits.")
		if len(value) > 500:
			raise ValueError(f"Value for {cell} exceeds the 500-character limit.")
		if cell in seen_cells:
			raise ValueError(f"Cell {cell} is listed more than once.")
		seen_cells.add(cell)
		assignments.append((cell, value))
		if len(assignments) > 50:
			raise ValueError("A single Excel task is limited to 50 cell assignments.")
	if not assignments:
		raise ValueError("Enter at least one cell assignment.")
	return assignments


def apply_cell_assignments(workbook_path, assignments):
	path = Path(workbook_path).expanduser().resolve()
	if path.suffix.lower() not in EXCEL_FILE_EXTENSIONS:
		raise ValueError("Only existing .xlsx and .xlsm workbooks are supported.")
	if not path.is_file():
		raise FileNotFoundError(f"Workbook not found: {path}")
	if not assignments:
		raise ValueError("No cell assignments were provided.")

	import pythoncom
	import win32com.client

	pythoncom.CoInitialize()
	excel = None
	workbook = None
	saved = False
	try:
		excel = win32com.client.DispatchEx("Excel.Application")
		excel.Visible = True
		excel.DisplayAlerts = True
		previous_security = excel.AutomationSecurity
		try:
			excel.AutomationSecurity = 3
			workbook = excel.Workbooks.Open(
				str(path),
				UpdateLinks=0,
				ReadOnly=False,
				IgnoreReadOnlyRecommended=True,
				AddToMru=False,
			)
		finally:
			excel.AutomationSecurity = previous_security
		if workbook.ReadOnly:
			raise PermissionError("Excel opened the workbook read-only; no cells were changed.")

		worksheet = workbook.ActiveSheet
		worksheet_name = str(worksheet.Name)
		for cell_address, value in assignments:
			cell = worksheet.Range(cell_address)
			cell.NumberFormat = "@"
			cell.Value2 = value
		workbook.Save()
		saved = True
		return f"Updated {len(assignments)} cell(s) on '{worksheet_name}' and saved {path.name}. The workbook remains open in Excel."
	finally:
		try:
			if not saved and excel is not None:
				if workbook is not None:
					workbook.Close(SaveChanges=False)
				excel.Quit()
		finally:
			pythoncom.CoUninitialize()
