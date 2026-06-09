# Marks `src` as a regular package (not a namespace package).
# Regular packages get per-module import locks, which prevents the
# `KeyError: 'src.utils'` race that occurs when multiple Streamlit session
# threads import this submodule concurrently on a cold start.
