# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import sys
from pathlib import Path

# Add the source directory to the path so autodoc can find the modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# -- Project information -----------------------------------------------------
project = "pyterm-mcp"
copyright = f"2026, Nicholas Corbin"  # noqa: A001
author = "Nicholas Corbin"
release = "0.1.0"

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",       # Auto-generate docs from docstrings
    "sphinx.ext.napoleon",      # Support Google/NumPy style docstrings
    "sphinx.ext.viewcode",      # Add links to source code
    "sphinx.ext.intersphinx",   # Link to other projects' docs
    "sphinx.ext.todo",          # Support for todo items
    "sphinx.ext.coverage",      # Check documentation coverage
     "myst_parser",              # Support Markdown files
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# The suffix of source filenames
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# The master toctree document
master_doc = "index"

# -- Options for HTML output -------------------------------------------------
html_theme = "furo"  # Modern, clean theme (install with: pip install furo)
html_static_path = ["_static"]
html_title = "pyterm-mcp Documentation"

# -- Extension configuration -------------------------------------------------

# autodoc settings
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}
autodoc_typehints = "description"
autodoc_typehints_format = "short"

# napoleon settings (for Google/NumPy style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = True
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_type_aliases = None

# intersphinx mapping - link to external documentation
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
}

# todo extension settings
todo_include_todos = True
