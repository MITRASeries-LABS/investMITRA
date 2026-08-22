content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = "f\"?? investMITRA MORNING BRIEF - {date.today()}\\\\n\\\\n\""
new = "f\"investMITRA MORNING BRIEF - {date.today()}\\\\n\\\\n\""

# Fix all ?? emojis in the morning brief section
import re
# Find the morning brief block and replace ?? with text equivalents
content = content.replace(
    'f"?? investMITRA MORNING BRIEF',
    'f"investMITRA MORNING BRIEF'
)
content = content.replace("'??' if sgx>0 else '??'", "'UP' if sgx>0 else 'DN'")
content = content.replace(
    "'??' if ctx['us_sentiment']=='POSITIVE' else '??' if ctx['us_sentiment']=='NEGATIVE' else '??'",
    "'UP' if ctx['us_sentiment']=='POSITIVE' else 'DN' if ctx['us_sentiment']=='NEGATIVE' else '--'"
)
content = content.replace(
    "'??' if nifty_change>0.3 else '??' if nifty_change<-0.3 else '??'",
    "'UP' if nifty_change>0.3 else 'DN' if nifty_change<-0.3 else '--'"
)
content = content.replace("f\"{ve} VIX:", "f\"VIX:")
content = content.replace("f\"{de} Direction:", "f\"Direction:")

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
