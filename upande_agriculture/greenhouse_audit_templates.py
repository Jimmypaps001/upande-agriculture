"""Checklists behind the Greenhouse Audit Template records seeded on install
and migrate, and the section -> table mapping of the two audit doctypes.
Each row is (section, look_at, look_for, observation); every observation is
scored 0 to 4.
"""

import frappe

SORT = "Sort / Seiri"
SET_IN_ORDER = "Set in Order / Seiton"
SHINE = "Shine / Seiso"
STANDARDIZE = "Standardize / Seiketsu"
SUSTAIN = "Sustain / Shitsuke"
PEOPLE_SAFETY = "People Safety"

_CORRIDOR = "Unnecessary items, litter, leaves, weeds. Sample one grower per GH, count beds weed, leaf and litter free as a percentage of that grower's total beds."
_BEDS = "Free of weeds, leaves, litter, rejects, knockings and suckers."
_HS_ORDER = "Well arranged in orderly manner."
_GH_ID = "Visible GH identifications as per standard with legends."
_GH_PERIPHERY = (
	"Environment around the greenhouse neatly kept clean (four sides weeded, grass well trimmed, no litter)."
)
_GH_CLEAN = "GH beds, pathways, passage, corridors cleanliness."
_HS_STANDARD = "Harvesting station at all greenhouses standardized as per parameters."

FIVE_S = [
	(SORT, "Corridor / Pathways", _CORRIDOR, "Leaves"),
	(SORT, "Corridor / Pathways", _CORRIDOR, "Weeds"),
	(SORT, "Corridor / Pathways", _CORRIDOR, "Litter"),
	(SORT, "Harvesting Station", "Free of litter, plant debris.", "Plant debris"),
	(SORT, "Harvesting Station", "Free of litter, plant debris.", "Litter"),
	(SORT, "Beds", _BEDS, "Weeds"),
	(SORT, "Beds", _BEDS, "Leaves"),
	(SORT, "Beds", _BEDS, "Suckers"),
	(SORT, "Beds", _BEDS, "Root stock"),
	(SORT, "Beds", _BEDS, "Litter"),
	(SORT, "Beds", _BEDS, "Reject"),
	(SORT, "Beds", _BEDS, "Knocking"),
	(
		SET_IN_ORDER,
		"Racks / Shelves",
		"All materials and items arranged in orderly manner, PEEP is visible.",
		"PEEP",
	),
	(SET_IN_ORDER, "Racks / Shelves", "Items identified with standard labels.", "Checks for labels"),
	(SET_IN_ORDER, "Harvesting Station", _HS_ORDER, "Nets"),
	(SET_IN_ORDER, "Harvesting Station", _HS_ORDER, "Cut stage container"),
	(SET_IN_ORDER, "Harvesting Station", _HS_ORDER, "Secateurs container"),
	(SET_IN_ORDER, "Harvesting Station", _HS_ORDER, "Buckets"),
	(SET_IN_ORDER, "Harvesting Station", _HS_ORDER, "Tables"),
	(SET_IN_ORDER, "Display Boards", _HS_ORDER, "Ref GH1 Kapkolia"),
	(
		SET_IN_ORDER,
		"Greenhouse",
		"Curtain straightened and above eye bolt, ropes well tied.",
		"Above eye bolt",
	),
	(
		SET_IN_ORDER,
		"Greenhouse",
		"Curtain straightened and above eye bolt, ropes well tied.",
		"Rope well tied",
	),
	(
		SET_IN_ORDER,
		"Greenhouse",
		"Curtain straightened and above eye bolt, ropes well tied.",
		"Curtain straightened",
	),
	(SET_IN_ORDER, "Greenhouse", "Skirting straightened and no openings.", "Straight skirt"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Bed number"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Bay number"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Grower identification"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Sprayer identification"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Harvesting station identification"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Irrigation valve identification"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Moth (Delta) trap identification"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "FCM"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Other moths"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Scouting flag"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Humidity hydrant lines"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Pesticide outlets"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Postharvest solution outlet"),
	(SET_IN_ORDER, "Greenhouse", _GH_ID, "Portable drinking water outlet outside greenhouse"),
	(SET_IN_ORDER, "Greenhouse", _GH_PERIPHERY, "Periphery of GH for weeding"),
	(SET_IN_ORDER, "Greenhouse", _GH_PERIPHERY, "Periphery of GH for well trimmed grass"),
	(
		SHINE,
		"Greenhouse",
		"Plastic with no dirt/dust/algae. Total bays clean over total bays of the GH.",
		"Check the floor and roof of the GH",
	),
	(
		SHINE,
		"Greenhouse",
		"Is the mandatory 10-minute cleaning activity followed at the end of every shift?",
		"Check for sign-off document from shift supervisor",
	),
	(SHINE, "Greenhouse", _GH_CLEAN, "Weeds free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Dry leaves free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Suckers free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Root stock free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Litter free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Knockings free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Rejects free"),
	(SHINE, "Greenhouse", _GH_CLEAN, "Water pool free"),
	(SHINE, "Harvesting Station", "Dry, no dust/dirt/mud. No litter.", "Check the harvesting stations"),
	(SHINE, "Display Boards", "No dust/mud.", "Check the display boards"),
	(SHINE, "Secateurs", "No dirt / stain.", "Check the secateurs"),
	(
		SHINE,
		"Disinfectant Containers",
		"Clean, no dirt / stain.",
		"Check for debris inside the disinfectant solution",
	),
	(
		SHINE,
		"External Drainage System & Skirting",
		"Clean, no dirt / stain.",
		"Check drainage and outside skirting around GH, free from blockages and weeds",
	),
	(SHINE, "Greenhouse Doors", "", "Check the GH doors for cleanliness"),
	(
		STANDARDIZE,
		"Standards for Disposal",
		"Standards fixed for disposal of materials e.g. plant debris, polythene waste, irrigation drain water for hydroponics GH, postharvest solution.",
		"Check for availability of SOPs and sample the know-how with 2 growers",
	),
	(
		STANDARDIZE,
		"Standard for GH",
		"No plastic should have torn openings on sides and top.",
		"Check for GH plastic openings",
	),
	(
		STANDARDIZE,
		"Standards for Crop Management",
		"Crop management e.g. desuckering, knockings, re-bending, rejects cutting and die-back removal.",
		"Check for availability of SOPs and sample adherence with at least 1 grower",
	),
	(STANDARDIZE, "Standard for Harvesting Station", _HS_STANDARD, "Table surface is white"),
	(STANDARDIZE, "Standard for Harvesting Station", _HS_STANDARD, "Black shade on top (80% shading effect)"),
	(
		STANDARDIZE,
		"Standard for Harvesting Station",
		_HS_STANDARD,
		"Calibrated marking of all stem lengths and all head sizes",
	),
	(
		STANDARDIZE,
		"Standard for Harvesting Station",
		_HS_STANDARD,
		"Holding points separately for mapal and bucket",
	),
	(
		STANDARDIZE,
		"Standard for Harvesting Station",
		_HS_STANDARD,
		"Cut stage chart and pest identification pictorials",
	),
	(
		STANDARDIZE,
		"Standard for Harvesting Station",
		_HS_STANDARD,
		"Defoliation fork and secateurs, disinfectant container",
	),
	(STANDARDIZE, "Standard for Harvesting Station", _HS_STANDARD, "Debris collector polythene bag / bin"),
	(
		STANDARDIZE,
		"Standards for Cleaning",
		"Standards for cleaning of secateurs, sorting/sizing tables.",
		"Check for the SOP of cleaning and adherence",
	),
	(
		SUSTAIN,
		"Knowledge on 5S",
		"All employees / staff are aware about 5S.",
		"Check for awareness by interview with growers and supervisors",
	),
	(
		SUSTAIN,
		"Top Management Involvement & Commitment",
		"Evidence of regular meeting / review for improvement.",
		"Check for 3C check sheet",
	),
	(
		SUSTAIN,
		"Top Management Involvement & Commitment",
		"Support by way of reward & recognition and resources available where required.",
		"Check with the supervisor on the R&R status for the GH",
	),
	(
		SUSTAIN,
		"NC Closure Rate",
		"Are all NCs identified during the previous audit closed before the next audit?",
		"Check for NC closure reports",
	),
	(
		SUSTAIN,
		"NC Repeat Rate",
		"Are previously closed NCs recurring in subsequent audits?",
		"Cross check current and closed NC reports",
	),
	(SUSTAIN, "5S Boards", "Zone map.", "Check if the zone map is available and updated"),
	(
		PEOPLE_SAFETY,
		"PPE & First Aid",
		"Are the required PPEs correctly used and is a personal first-aid kit readily available?",
		"Correct PPE usage and availability of a complete first aid box",
	),
	(
		PEOPLE_SAFETY,
		"Visual Safety Boards",
		"Are visual safety boards displayed at designated locations and effectively used?",
		"Visual boards displaying correct and incorrect practices",
	),
	(
		PEOPLE_SAFETY,
		"PPE & Safety Information",
		"Are PPE-related photos and safety information for the GH displayed at designated locations?",
		"PPE-wearing instructions and key GH safety guidelines displayed",
	),
]

DWM = "Daily Work Management"
KPI = "Team Performance (KPI)"
GH_SAFETY = "GH Safety"

_TEAM_BOARD = "Check if team boards are updated daily."

DMS = [
	(DWM, "Updating Team Board", _TEAM_BOARD, "Attendance"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "Production"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "Rejection"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "3C"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "Skill matrix"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "Safety chart"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "Customer complaint"),
	(DWM, "Updating Team Board", _TEAM_BOARD, "5S score above"),
	(DWM, "Updating Team Board", "Check on ERP system.", "GH cultural activity plan update"),
	(KPI, "Production", "Production achieved vs budget for the week.", "Production display - KPI board"),
	(KPI, "FTR (Weekly Trend)", "Check on ERP system.", "FTR display - ERP"),
	(
		KPI,
		"Forecast Accuracy",
		"Percentage forecast accuracy for the week on ERP.",
		"Forecast accuracy - KPI board",
	),
	(KPI, "6S Sustenance", "Check for 6S sustenance (<80%).", "Check for 6S score sheet"),
	(
		GH_SAFETY,
		"Signages",
		"Safety signages present and visible: humidity, spray line outlet, re-entry boards. 0 if a single sign is missing.",
		"Humidity",
	),
	(
		GH_SAFETY,
		"Signages",
		"Safety signages present and visible: humidity, spray line outlet, re-entry boards. 0 if a single sign is missing.",
		"Spray",
	),
	(
		GH_SAFETY,
		"Signages",
		"Safety signages present and visible: humidity, spray line outlet, re-entry boards. 0 if a single sign is missing.",
		"Re-entry board",
	),
	(GH_SAFETY, "GH Doors", "All doors lockable. 0 if one or more doors not lockable.", "Check doors"),
	(
		GH_SAFETY,
		"GH Pathways / Corridors",
		"Free of high water content mud.",
		"Check the passage / corridors",
	),
	(GH_SAFETY, "Spray Outlets", "Check for leakages. 0 if any leakage spotted.", "Check spray hydrants"),
]

FIVE_S_SECTIONS = {
	SORT: "sort",
	SET_IN_ORDER: "set_in_order",
	SHINE: "shine",
	STANDARDIZE: "standardize",
	SUSTAIN: "sustain",
	PEOPLE_SAFETY: "people_safety",
}
DMS_SECTIONS = {DWM: "daily_work_management", KPI: "team_performance", GH_SAFETY: "gh_safety"}

# audit_type -> (audit doctype, {section: table fieldname})
AUDIT_TYPES = {
	"5S": ("Greenhouse 5S Audit", FIVE_S_SECTIONS),
	"DMS": ("Greenhouse DMS Audit", DMS_SECTIONS),
}

TEMPLATES = {"5S Audit": ("5S", FIVE_S), "DMS Audit": ("DMS", DMS)}


SCORES = (0, 1, 2, 3, 4)


def seed():
	"""Insert the shipped scores and templates that don't exist yet; never
	touches ones already on the site, so edits made there are kept."""
	for score in SCORES:
		if not frappe.db.exists("Greenhouse Audit Score", str(score)):
			frappe.get_doc({"doctype": "Greenhouse Audit Score", "score": score}).insert(
				ignore_permissions=True
			)
	for name, (audit_type, rows) in TEMPLATES.items():
		if frappe.db.exists("Greenhouse Audit Template", name):
			continue
		frappe.get_doc(
			{
				"doctype": "Greenhouse Audit Template",
				"template_name": name,
				"audit_type": audit_type,
				"items": [
					{"section": s, "look_at": a, "look_for": f, "observation": o, "max_score": 4}
					for s, a, f, o in rows
				],
			}
		).insert(ignore_permissions=True)
