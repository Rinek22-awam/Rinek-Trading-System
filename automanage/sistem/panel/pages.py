"""Notebook pages alongside the existing bot dashboard."""
from tkinter import ttk

from .page_settings import SettingsPage
from .page_sources import SourcePage
from .page_tools import ToolsPage


def add_pages(root, tabs):
    ttk.Style(root).configure("Menu.TButton", anchor="w", padding=(8, 4))
    SourcePage(root, tabs)
    SettingsPage(root, tabs)
    ToolsPage(root, tabs)
