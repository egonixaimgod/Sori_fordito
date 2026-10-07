"""Magyar Felirat Fordító - MKV feliratok fordítása magyarra, helyben."""

__version__ = "2.0.0"

# A kiadás-szkript (rebuild_es_github_push.bat -> bump_build.py) minden kiadásnál
# eggyel növeli. Kézzel ne írd át: a GitHubon publikált számnál kisebbre állítva
# a következő kiadás úgyis onnan folytatja.
BUILD_SZAM = 2

__all__ = ["__version__", "BUILD_SZAM"]
