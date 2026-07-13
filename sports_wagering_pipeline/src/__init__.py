"""Sports Wagering, DFS & Pick'em automation pipeline.

A flat, self-contained comparison pipeline (SQLite cache + PuLP DFS optimizer
+ CDF-based Pick'em edge math). Deliberately separate from the repo's main
``project547`` engine so the two workflows can be run side by side.
"""
