from datetime import date

from settlement_reminders.rules import due_date, reminder_day, select_installments, should_send
from tests.factories import make_agreement


def test_installment_expansion():
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    installments = agreement.installments()
    assert len(installments) == 3
    assert installments[0].original_due == date(2026, 6, 15)
    assert installments[1].original_due == date(2026, 7, 15)
    assert installments[2].original_due == date(2026, 8, 15)
    assert installments[2].is_last is True


def test_due_date_moves_to_a_business_day():
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    third = agreement.installments()[2]  # 15/08/2026 (Saturday)
    assert due_date(third) == date(2026, 8, 17)


def test_should_send_3_business_days_before_the_due_date():
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    second = agreement.installments()[1]  # due 15/07/2026 (Wednesday)
    # 3 BUSINESS days before: Tue 14, Mon 13, Fri 10/07
    assert reminder_day(second, 3) == date(2026, 7, 10)
    assert should_send(second, date(2026, 7, 10), 3) is True
    assert should_send(second, date(2026, 7, 12), 3) is False  # 3 calendar days (Sunday)
    assert should_send(second, date(2026, 7, 13), 3) is False


def test_catchup_recovers_a_missed_run_without_passing_the_due_date():
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    third = agreement.installments()[2]  # due 17/08 (Mon); reminder day 12/08 (Wed)

    # the next day (Thu 13/08) recovers with catch-up, but not without it
    assert should_send(third, date(2026, 8, 13), 3) is False
    assert should_send(third, date(2026, 8, 13), 3, catchup_days=2) is True

    # Saturday 15/08 does not send (not a business day), even inside the window
    assert should_send(third, date(2026, 8, 15), 3, catchup_days=5) is False

    # inside the window and up to the due date (Mon 17/08) sends; afterwards, never
    assert should_send(third, date(2026, 8, 17), 3, catchup_days=5) is True
    assert should_send(third, date(2026, 8, 18), 3, catchup_days=30) is False


def test_selection_considers_the_postponement():
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    # the 3rd is due 15/08 (Saturday) -> due date 17/08 (Monday);
    # 3 business days before: Fri 14, Thu 13, Wed 12/08
    selected = select_installments(agreement.installments(), date(2026, 8, 12), 3)
    assert len(selected) == 1
    assert selected[0].number == 3
    assert select_installments(agreement.installments(), date(2026, 8, 14), 3) == []
