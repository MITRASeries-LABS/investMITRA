content = open('scripts/weight_optimizer.py', encoding='utf-8').read()

old = 'def save_new_weights(opus_result: dict, stats: dict):'

new = '''def save_new_weights(opus_result: dict, stats: dict):
    """
    IMPORTANT: Only save weights if confidence > 0.7 AND trades > 20.
    With low trade count, Opus suggestions go to Telegram only ? not auto-applied.
    This prevents over-fitting on small data.
    """
    if stats.get("total", 0) < 20:
        # Not enough data ? send suggestions to Telegram only
        try:
            from order_manager import notify
            suggestions = opus_result.get("key_findings", [])
            notes = opus_result.get("notes", "")
            notify(
                f"OPUS WEEKLY SUGGESTIONS (not auto-applied)\n"
                f"Trades: {stats['total']} (need 20+ to auto-apply)\n\n"
                f"Findings:\n" + "\n".join([f"- {f}" for f in suggestions[:5]]) +
                f"\n\nNotes: {notes[:200]}\n\n"
                f"Review manually and apply if agree."
            )
        except: pass
        print(f"  Only {stats['total']} trades ? suggestions sent to Telegram, NOT auto-applied")
        return

    confidence = opus_result.get("confidence", 0)
    if confidence < 0.7:
        print(f"  Low confidence ({confidence:.0%}) ? not auto-applying weights")
        return

def save_new_weights(opus_result: dict, stats: dict):'''

if old in content:
    content = content.replace(old, new, 1)
    open('scripts/weight_optimizer.py', 'w', encoding='utf-8').write(content)
    print('Opus role fixed - suggestions only until 20+ trades!')
else:
    print('Pattern not found')
