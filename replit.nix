{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    # WeasyPrint / PDF system dependencies
    pkgs.cairo
    pkgs.pango
    pkgs.gdk-pixbuf
    pkgs.gobject-introspection
    pkgs.pkg-config
    pkgs.shared-mime-info
    # Fonts for PDF rendering
    pkgs.dejavu_fonts
    # SQLite (used for user auth & deal storage)
    pkgs.sqlite
  ];
}
