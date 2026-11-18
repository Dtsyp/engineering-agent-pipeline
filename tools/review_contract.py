"""Shared verdict contract for native CLI submissions and host-returned reviews."""


def validate_report(report, against):
    if not isinstance(report, dict) or report.get('against') != against:
        raise ValueError('reviewer did not return the requested input identifier')
    verdict = report.get('verdict')
    checked = report.get('checked')
    findings = report.get('findings')
    blockers = report.get('blockers')
    if (verdict not in ('FIX', 'PASS') or type(blockers) is not int or blockers < 0
            or not isinstance(checked, list) or not checked
            or not all(isinstance(item, str) and item.strip() for item in checked)
            or not isinstance(findings, list)):
        raise ValueError('incomplete reviewer verdict, blockers, findings or checked evidence')
    for finding in findings:
        if (not isinstance(finding, dict) or finding.get('severity') not in ('BLOCKER', 'MAJOR', 'MINOR')
                or not all(isinstance(finding.get(key), str) and finding[key].strip()
                           for key in ('location', 'evidence', 'impact', 'suggested_fix'))):
            raise ValueError('malformed review finding')
    if verdict == 'PASS' and (blockers or any(f['severity'] != 'MINOR' for f in findings)):
        raise ValueError('PASS contradicts blockers or material findings')
    if blockers != sum(f['severity'] == 'BLOCKER' for f in findings):
        raise ValueError('blocker count must match the BLOCKER findings')
    if verdict == 'FIX' and not findings:
        raise ValueError('FIX needs actionable findings')
    return report
