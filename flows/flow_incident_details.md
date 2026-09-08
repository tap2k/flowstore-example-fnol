---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_in_offtopic
    condition: Caller has gone off on a tangent unrelated to this accident — asking about other Northwind products, an unrelated policy change, or general chit-chat that isn't about what happened.
    notes: Routes to the off-topic handler, a callable flow (goto RETURN) — distinct from the global interrupts. Reached only from this flow, the most open-ended point in intake, where callers most often wander.
    goto: flow_off_topic
  - id: xp_in_to_vehicle
    condition: Caller has shared when, where, what happened, their own sense of fault (or that they're unsure), and whether the police were involved.
    goto: flow_vehicle_info
---
# Incident details

Get the basics of what happened: when, where, a brief description, the caller's fault assessment, and whether police were involved. Let the caller volunteer info in any order — don't drill on specifics they don't mention. Capture 'incident_when' (free-form date/time as they describe it), 'incident_location' (address, intersection, or landmark), 'incident_description' (one or two sentences), 'fault_assessment' (self / other / shared / unclear — capture only what they volunteer, NEVER tell them what you think), 'police_report_filed' (boolean), and if filed, 'police_report_number'. The claim-types reference can help you recognize what kind of incident this is, but never quote a coverage decision — that's the adjuster's call. Keep your follow-ups short. If they're not sure about fault, mark 'unclear' and move on — that's expected.

## Scripts

### in_s1
- en-US: Walk me through what happened — when and where, just the basics. I'll ask follow-ups if I need them.
- es-US: Cuénteme qué pasó — cuándo y dónde, solo lo básico. Le hago más preguntas si las necesito.

### in_s2
- en-US: Got it. Was anyone else's car involved, or just yours?
- es-US: Entendido. ¿Hubo otro auto involucrado, o solo el suyo?

### in_s3
- en-US: Did the police come out, or no?
- es-US: ¿Vino la policía, o no?

### in_s4
- en-US: Do you have the report number, or should we follow up with the station later?
- es-US: ¿Tiene el número de reporte, o lo consultamos con la estación más tarde?
