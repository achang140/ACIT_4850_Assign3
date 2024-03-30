def call(service) {
    pipeline {
        agent any // Execute on any available agent 

        parameters {
            booleanParam(defaultValue: false, description: 'Deploy the App', name: 'DEPLOY') 
        }

        stages {

            stage ('Build') {
                steps {
                    sh "rm -rf venv || true"
                    sh '''
                        python3 -m venv ./venv
                        . ./venv/bin/activate
                        python3 -m pip install --upgrade pip
                        ./venv/bin/pip install safety
                        ./venv/bin/pip install bandit
                    '''
                }
            }

            stage('Python Lint') {
                steps {
                    dir ("${WORKSPACE}/${service}") {
                        sh "pylint --fail-under 5 *.py"
                    }
                }
            }

            stage ('Security Scan - Python Dependencies') {
                steps {
                    dir ("${WORKSPACE}/${service}") {
                        sh ". ${WORKSPACE}/venv/bin/activate && safety check"
                    }
                }
            }

            stage ('Security Scan - Python Code') {
                steps {
                    dir ("${WORKSPACE}/${service}") {
                        sh ". ${WORKSPACE}/venv/bin/activate && bandit -r ."
                    }
                }
            }

            stage ('Package') {
                when {
                    expression {env.GIT_BRANCH == 'origin/main'}
                }
                steps {
                    dir ("${WORKSPACE}/${service}") {
                        withCredentials([string(credentialsId: 'DockerHubAmanda', variable: 'TOKEN')]) {
                            sh "docker login -u 'achang99' -p '$TOKEN' docker.io"
                            sh "docker build -t achang99/${service}:latest ."
                            sh "docker push achang99/${service}:latest"
                        }
                    }
                }
            }

            stage ('Deploy') {
                when {
                    expression { params.DEPLOY }
                }
                steps {
                    sshagent(credentials: ['SSHAmanda']) {
                        sh "ssh -o StrictHostKeyChecking=no azureuser@acit3855-lab6-kafka.westus3.cloudapp.azure.com 'cd Assignment3/amanda_assign3/deployment && docker-compose pull ${service} && docker-compose up -d'"
                    }
                }
            }
        }
    }
}
