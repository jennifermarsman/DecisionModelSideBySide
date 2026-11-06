"""Default scenarios: each has a data.config file and a default set of labels."""

from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SCENARIOS = {
    "customer": {
        "name": "Customer questions & complaints",
        "description": "Route incoming customer messages to the right Microsoft sales or support team.",
        "data_file": DATA_DIR / "customer" / "data.config",
        "labels": [
            {"name": "Sales - New Purchase", "description": "Wants to buy a product, subscription, or license, or asks about pricing and quotes."},
            {"name": "Sales - Product Comparison", "description": "Asks which product, plan, or edition fits their needs, or compares offerings."},
            {"name": "Billing & Licensing", "description": "Questions or complaints about invoices, charges, refunds, renewals, or license assignment."},
            {"name": "Technical Support", "description": "Needs help with an error, outage, installation, configuration, or something not working."},
            {"name": "Account & Access", "description": "Sign-in problems, password resets, MFA, locked or compromised accounts."},
            {"name": "Feedback & Complaints", "description": "General complaints, praise, or feedback about service or experience, without a specific request."},
            {"name": "Other", "description": "Anything that does not fit the other categories."},
        ],
    },
    "expenses": {
        "name": "Expense report classification",
        "description": "Categorize expense report line items for reimbursement and accounting.",
        "data_file": DATA_DIR / "expenses" / "data.config",
        "labels": [
            {"name": "Lodging", "description": "Hotels, resorts, short-term rentals, and related room charges or fees."},
            {"name": "Airfare", "description": "Flights, airline tickets, seat upgrades, and baggage fees."},
            {"name": "Ground Transportation", "description": "Taxis, Uber/Lyft rides, trains, buses, parking, tolls, and fuel."},
            {"name": "Car Rental", "description": "Rental car charges, rental insurance, and rental car fuel or fees."},
            {"name": "Meals & Entertainment", "description": "Restaurants, coffee, room service, client dinners, and entertainment."},
            {"name": "Office Supplies & Equipment", "description": "Stationery, printing, hardware, peripherals, and other supplies."},
            {"name": "Software & Subscriptions", "description": "Software licenses, SaaS subscriptions, and online services."},
            {"name": "Conferences & Training", "description": "Event registrations, conference fees, courses, and certifications."},
            {"name": "Communications", "description": "Phone, mobile data, roaming, and internet or Wi-Fi charges."},
            {"name": "Other", "description": "Anything that does not fit the other categories."},
        ],
    },
}


def load_data(path):
    """Read a data.config file: one item per line; blank lines and lines starting with '#' are ignored."""
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                items.append(line)
    return items
