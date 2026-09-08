---
version: 0.1.0
type: interrupt
entry_condition: Caller says they want to cancel, don't want to file after all, or were just looking for information.
exit_paths:
  - id: xp_icc_done
    condition: Caller has answered whether to leave a note on the policy.
    actions:
      - cap_log_no_file
    goto: END
---
# Interrupt — cancel / never mind

The caller wants to cancel or was just looking for information. Confirm without pushing and ask whether to leave a note on the policy that they reached out, in case they decide to file later. Capture 'leave_note' (boolean) and fire log_no_file (the backend decides what to do with leave_note).

## Scripts

### icc_s1
- en-US: No problem — you can always call back. Want me to leave a note on the policy that you reached out, in case you decide to file later?
- es-US: No hay problema — siempre puede volver a llamar. ¿Quiere que deje una nota en la póliza de que se comunicó, por si decide presentarla después?

### icc_s2
- en-US: Take care — call us back whenever.
- es-US: Cuídese — llámenos cuando guste.
