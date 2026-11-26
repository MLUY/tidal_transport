from viktor.core import ViktorController


class Project(ViktorController):
    label = 'Project'
    children = ['TransportPlan']
    show_children_as = 'Table'
