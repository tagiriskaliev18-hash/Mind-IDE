import QtQuick 2.15
import QtQuick.Layouts 1.15
import org.kde.plasma.plasmoid 2.0
import org.kde.plasma.core 2.0 as PlasmaCore
import org.kde.plasma.components 3.0 as PlasmaComponents
import org.kde.ksysguard.sensors 1.0 as Sensors

Item {
    id: root

    Plasmoid.preferredRepresentation: Plasmoid.compactRepresentation
    Plasmoid.toolTipMainText: "AIsktag Mind & Dev HUD"
    Plasmoid.toolTipSubText: aiActive ? "Модель: Активна" : "Модель: Спит"

    property bool aiActive: false

    PlasmaCore.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: function(sourceName, data) {
            var out = data["stdout"].trim();
            root.aiActive = (out === "active" || out === "activating");
            disconnectSource(sourceName);
        }
        function checkStatus() {
            connectSource("systemctl is-active aisktag-llm-backend.service");
        }
    }

    Timer {
        interval: 2000
        running: true
        repeat: true
        onTriggered: executable.checkStatus()
    }

    Sensors.Sensor { id: cpuSensor; sensorId: "cpu/all/usage"; updateRateLimit: 1000 }
    Sensors.Sensor { id: ramSensor; sensorId: "memory/physical/used"; updateRateLimit: 1000 }
    Sensors.Sensor { id: ramTotalSensor; sensorId: "memory/physical/total"; updateRateLimit: 1000 }
    Sensors.Sensor { id: tempSensor; sensorId: "cpu/all/averageTemperature"; updateRateLimit: 1000 }

    Plasmoid.compactRepresentation: Item {
        PlasmaCore.IconItem {
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height)
            height: width
            source: "aisktagos-logo"
            opacity: root.aiActive ? 1.0 : 0.4
        }
        MouseArea {
            anchors.fill: parent
            onClicked: plasmoid.expanded = !plasmoid.expanded
        }
    }

    Plasmoid.fullRepresentation: Item {
        Layout.minimumWidth: 250
        Layout.minimumHeight: 180

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 12

            PlasmaComponents.Label {
                text: "Dev HUD"
                font.bold: true
                font.pointSize: 14
            }

            RowLayout {
                PlasmaComponents.Label { text: "ИИ Mind:" ; Layout.fillWidth: true }
                PlasmaComponents.Label {
                    text: root.aiActive ? "В памяти" : "Спит"
                    color: root.aiActive ? "#5be37a" : "#8f9abf"
                    font.bold: true
                }
            }

            RowLayout {
                PlasmaComponents.Label { text: "CPU:" ; Layout.fillWidth: true }
                PlasmaComponents.Label { text: cpuSensor.formattedValue || "—" }
            }

            RowLayout {
                PlasmaComponents.Label { text: "RAM:" ; Layout.fillWidth: true }
                PlasmaComponents.Label { text: (ramSensor.formattedValue || "—") + " / " + (ramTotalSensor.formattedValue || "—") }
            }

            RowLayout {
                PlasmaComponents.Label { text: "Temp:" ; Layout.fillWidth: true }
                PlasmaComponents.Label { text: tempSensor.formattedValue || "—" }
            }

            Item { Layout.fillHeight: true }
        }
    }
}
