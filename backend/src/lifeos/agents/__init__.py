"""AI teammates on the GitHub boards.

The vault holds the configuration: ``boards/<board>.md`` (board contracts: columns, owners,
fields, README) and ``agents/<name>.md`` (agent definitions). The worker mirrors the
contracts to GitHub and lets agents take tickets that sit in an AI column, following the
five rules in the contracts: take free tickets, announce and report, move only to review,
block with a comment when stuck, answer every comment of the owner.
"""
