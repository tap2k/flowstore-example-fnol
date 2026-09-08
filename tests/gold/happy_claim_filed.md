---
name: Happy path — claim filed and callback scheduled
source_pointer: Authored reference for the fnol happy path; mirrors flow_safety_triage → flow_identify → flow_incident_details → flow_vehicle_info → flow_other_party_info → flow_photos → flow_review_and_file → flow_schedule_adjuster.
tags:
  - lang:en
  - happy-path
vars:
  now: 2026-06-16
mocks:
  cap_verify_policy:
    kind: static
    returns:
      policy_active: true
      deductible_amount: 500
      named_drivers: Jordan Reese
  cap_file_claim:
    kind: static
    returns:
      claim_id: NW-2026-018472
      estimated_callback_window: 2 hours
---
Caller is safe and unhurt, verifies an active policy, reports a rear-end collision with another party, confirms the recap, the claim is filed, and a callback is scheduled.

## Transcript

Agent: Hi, Northwind claims line — this is Quinn. Before anything else, is everyone okay? Are you somewhere safe right now?
User: Yeah, everyone's fine. I'm pulled over on the shoulder.
Agent: Good — I'm glad you're safe. Can I get your name and policy number? Take your time.
User: Jordan Reese, policy seven seven four two one zero nine.
Agent: Let me read that back: 7-7-4-2-1-0-9. Sound right?
User: That's it.
Agent: Thanks, Jordan. Walk me through what happened — when and where, just the basics.
User: About twenty minutes ago, someone rear-ended me at the light on Oak and 3rd. I think it was their fault but I'm not totally sure.
Agent: Got it — no worries if you're not sure on that. Did the police come out?
User: No police. The other driver's name is Sam Avery, I've got their number but not their insurance.
Agent: That's fine, we'll work with what we have. How about your car — is it drivable, and what areas got hit?
User: Rear bumper's crunched but it still drives.
Agent: If you've got photos of the damage and the scene, we'd love them — I can text you a link after we file. Sound good?
User: Yeah, I can send photos.
Agent: Let me read this back: about twenty minutes ago at Oak and 3rd, rear-ended at a light, rear bumper damage, car still drivable, other driver Sam Avery with a phone number, and you'll send photos. Does that all sound right?
User: Yes, that's right.
Agent: Filing the claim now — give me one second. You're all filed — claim NW-2026-018472. An adjuster will reach out in about 2 hours. What number's best to reach you?
User: This number is fine, five five five one two one two.
Agent: Let me read that back: 5-5-5-1-2-1-2. And when works best — as soon as possible, this morning, this afternoon, or tomorrow?
User: As soon as possible.
Agent: You're all set. Nothing else you need to do — your adjuster will reach out at 5-5-5-1-2-1-2. Take care.
