#utils.py → PDF extraction & formatting helpers

import fitz  # PyMuPDF
import pandas as pd
import os
import json
import re



def extract_text_from_pdf(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    text = "".join(page.get_text() for page in doc)
    return text


def extract_text_from_file(file_path: str) -> str:
    """Extract text from PDF, TXT, or Markdown."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: {ext}")



def format_list_field_as_html(val):
    if isinstance(val, list):
        items = "".join(f"<li>{str(item).strip()}</li>" for item in val)
        return f"<ul style='padding-left:20px'>{items}</ul>"
    return val if val is not None else ""

def save_dataframe_as_html(df: pd.DataFrame, html_path: str, title: str):
    styled_df = df.style.set_table_styles([
        {'selector': 'th', 'props': [('background-color', '#4CAF50'),
                                     ('color', 'white'),
                                     ('font-weight', 'bold'),
                                     ('padding', '10px'),
                                     ('text-align', 'left')]},
        {'selector': 'td', 'props': [('padding', '8px'),
                                     ('border-bottom', '1px solid #ddd')]},
        {'selector': 'tr:nth-child(even)', 'props': [('background-color', '#f9f9f9')]},
        {'selector': 'tr:hover', 'props': [('background-color', '#f1f1f1')]}
    ]).set_properties(**{
        'border': '1px solid #ddd',
        'text-align': 'left',
        'font-family': 'Arial, sans-serif',
        'font-size': '14px',
        'white-space': 'pre-wrap',
        'word-break': 'break-word'
    })

    styled_html = styled_df.to_html(escape=False)

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
    body {{
        font-family: Arial, sans-serif;
        margin: 20px;
        background-color: #fafafa;
    }}
    h1 {{
        color: #333;
        text-align: center;
    }}
    .scroll-container {{
        max-width: 100%;
        overflow-x: auto;
        border: 1px solid #ccc;
        border-radius: 8px;
        background-color: white;
        padding: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}
    table {{
        border-collapse: collapse;
        width: 100%;
    }}
    th, td {{
        vertical-align: top;
    }}
    ul {{
        margin: 0;
        padding-left: 20px;
        list-style-type: disc;
    }}
    li {{
        margin-bottom: 4px;
    }}
</style>
</head>
<body>
    <h1>{title}</h1>
    <div class="scroll-container">
        {styled_html}
    </div>
</body>
</html>
"""

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_template)





def save_interactive_matrix_html(json_obj, html_path: str, title: str = "FAIRagro Application-Data-Matrix"):
    """
    Save the given JSON object into the interactive HTML matrix viewer.
    """
    import pathlib

    template_path = os.path.join(pathlib.Path(__file__).parent, "interactive_template.html")


    #template_path = "interactive_template.html"
    with open(template_path, "r", encoding="utf-8") as f:
        template_html = f.read()

    # Replace placeholder with JSON
    injected_html = template_html.replace("__DATA_PLACEHOLDER__", json.dumps(json_obj, indent=2, ensure_ascii=False))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(injected_html)




    # Save JSON separately
    json_path = os.path.splitext(html_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_obj, f, indent=2, ensure_ascii=False)

    return html_path, json_path

