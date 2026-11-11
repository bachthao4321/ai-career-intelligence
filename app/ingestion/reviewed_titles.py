"""Manually reviewed title translations, keyed by the exact source title.

A source title change never inherits a previous review.  The catalog is deliberately
conservative; model-generated translations do not become reviewed automatically.
"""

REVIEWED_TITLES: dict[str, tuple[str, str]] = {
    "Professional Front-End Developer React & TypeScript - 20593": (
        "Professional Front-End Developer React & TypeScript - 20593",
        "Lập trình viên Frontend (React & TypeScript) - 20593",
    ),
    "Frontendutvecklare": ("Frontend Developer", "Lập trình viên Frontend"),
    "Senior Frontend Developer": ("Senior Frontend Developer", "Lập trình viên Frontend cấp cao"),
    "Junior Frontendutvecklare": ("Junior Frontend Developer", "Lập trình viên Frontend Junior"),
    "Frontendutvecklare – React och TypeScript": (
        "Frontend Developer – React and TypeScript",
        "Lập trình viên Frontend – React và TypeScript",
    ),
    "Frontendutvecklare – React & TypeScript": (
        "Frontend Developer – React & TypeScript",
        "Lập trình viên Frontend – React & TypeScript",
    ),
    "Front-end Developer React & TypeScript": (
        "Front-end Developer React & TypeScript",
        "Lập trình viên Frontend React & TypeScript",
    ),
    "Software Engineer Frontend Development - 20739": (
        "Software Engineer Frontend Development - 20739",
        "Kỹ sư phần mềm Frontend - 20739",
    ),
    "Experienced Frontend Developer TypeScript | Svelte | Playwright": (
        "Experienced Frontend Developer TypeScript | Svelte | Playwright",
        "Lập trình viên Frontend có kinh nghiệm: TypeScript | Svelte | Playwright",
    ),
    "Backendutvecklare": ("Backend Developer", "Lập trình viên Backend"),
    "Backendutvecklare Java": ("Backend Developer Java", "Lập trình viên Backend Java"),
    "Java-backendutvecklare": ("Java Backend Developer", "Lập trình viên Backend Java"),
    "Senior Backendutvecklare Java": (
        "Senior Backend Developer Java",
        "Lập trình viên Backend Java cấp cao",
    ),
    "Backend Developer": ("Backend Developer", "Lập trình viên Backend"),
    "Fullstackutvecklare": ("Full-stack Developer", "Lập trình viên Full-stack"),
    "Senior Fullstackutvecklare": (
        "Senior Full-stack Developer",
        "Lập trình viên Full-stack cấp cao",
    ),
    "Senior fullstackutvecklare": (
        "Senior Full-stack Developer",
        "Lập trình viên Full-stack cấp cao",
    ),
    "Senior Fullstack-utvecklare (.NET, Vue.js & TypeScript), Distans": (
        "Senior Full-stack Developer (.NET, Vue.js & TypeScript), Remote",
        "Lập trình viên Full-stack cấp cao (.NET, Vue.js & TypeScript), làm việc từ xa",
    ),
    "Senior Fullstackutvecklare med fokus på Next.js": (
        "Senior Full-stack Developer focused on Next.js",
        "Lập trình viên Full-stack cấp cao, tập trung vào Next.js",
    ),
    "Senior fullstackutvecklare .NET": (
        "Senior Full-stack Developer .NET",
        "Lập trình viên Full-stack .NET cấp cao",
    ),
    "Full-Stack Developer": ("Full-Stack Developer", "Lập trình viên Full-stack"),
    "Full Stack Developer (TypeScript/React)": (
        "Full Stack Developer (TypeScript/React)",
        "Lập trình viên Full-stack (TypeScript/React)",
    ),
    "Fullstackutvecklare med frontendfokus": (
        "Full-stack Developer with Frontend Focus",
        "Lập trình viên Full-stack, tập trung vào Frontend",
    ),
    "Full Stack Product Engineer": ("Full Stack Product Engineer", "Kỹ sư sản phẩm Full-stack"),
    "Full-stack Engineer": ("Full-stack Engineer", "Kỹ sư Full-stack"),
    "Tech Lead Full-Stack Rails Engineer": (
        "Tech Lead Full-Stack Rails Engineer",
        "Tech Lead kiêm kỹ sư Full-stack Rails",
    ),
    "Full-Stack Developer Marketing Automation - 20619": (
        "Full-Stack Developer Marketing Automation - 20619",
        "Lập trình viên Full-stack, tự động hóa marketing - 20619",
    ),
    "Fullstackutvecklare till konsultbolag i Stockholm": (
        "Full-stack Developer for a consulting company in Stockholm",
        "Lập trình viên Full-stack cho công ty tư vấn tại Stockholm",
    ),
    "Fullstack webbutvecklare (React) till Unipower": (
        "Full-stack Web Developer (React) at Unipower",
        "Lập trình viên web Full-stack (React) tại Unipower",
    ),
    "Scrum Master": ("Scrum Master", "Scrum Master"),
    "Scrum Master (Lund)": ("Scrum Master (Lund)", "Scrum Master (Lund)"),
    "Senior Software Architect": ("Senior Software Architect", "Kiến trúc sư phần mềm cấp cao"),
    "Embedded Software Architect": ("Embedded Software Architect", "Kiến trúc sư phần mềm nhúng"),
    "Senior Software Architect inom MedTech": (
        "Senior Software Architect in MedTech",
        "Kiến trúc sư phần mềm cấp cao trong lĩnh vực MedTech",
    ),
    "Project Manager – Information Technology": (
        "Project Manager – Information Technology",
        "Quản lý dự án – Công nghệ thông tin",
    ),
    "Project Manager – AI & Business Systems": (
        "Project Manager – AI & Business Systems",
        "Quản lý dự án – AI & Hệ thống kinh doanh",
    ),
    "Technical Project Manager": ("Technical Project Manager", "Quản lý dự án kỹ thuật"),
    "Projektledare IT": ("IT Project Manager", "Quản lý dự án IT"),
    "Teknisk projektledare": ("Technical Project Manager", "Quản lý dự án kỹ thuật"),
    "Data Scientist": ("Data Scientist", "Chuyên gia khoa học dữ liệu"),
    "Data scientist": ("Data scientist", "Chuyên gia khoa học dữ liệu"),
    "Senior Data Scientist": ("Senior Data Scientist", "Chuyên gia khoa học dữ liệu cấp cao"),
    "Embedded Software Engineer": ("Embedded Software Engineer", "Kỹ sư phần mềm nhúng"),
    "DevOps Engineer": ("DevOps Engineer", "Kỹ sư DevOps"),
    "Software Engineer": ("Software Engineer", "Kỹ sư phần mềm"),
    "Senior Software Developer": ("Senior Software Developer", "Lập trình viên phần mềm cấp cao"),
    "Junior Web Developer": ("Junior Web Developer", "Lập trình viên Web Junior"),
    "Kvalitetsutvecklare med fokus på IT, säkerhet och leverantörsstyrning": (
        "Quality Developer focused on IT, security and supplier management",
        "Chuyên viên phát triển chất lượng, tập trung vào IT, bảo mật và quản lý nhà cung cấp",
    ),
    "Förvaltningsledare / Projektledare IT": (
        "IT Management Lead / Project Manager",
        "Trưởng bộ phận quản lý hệ thống / Quản lý dự án IT",
    ),
}
